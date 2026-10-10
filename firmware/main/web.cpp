#include "web.h"

#include <cstring>
#include <memory>
#include <string>

#include "api.h"
#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "net.h"

static const char* TAG = "web";

extern const char index_html_start[] asm("_binary_index_html_start");
extern const char index_html_end[] asm("_binary_index_html_end");

namespace {
App* g_app = nullptr;
httpd_handle_t g_server = nullptr;

struct Locked {
  Locked() { xSemaphoreTake(g_app->lock, portMAX_DELAY); }
  ~Locked() { xSemaphoreGive(g_app->lock); }
};

using JsonPtr = std::unique_ptr<cJSON, decltype(&cJSON_Delete)>;

HwInfo Hw() {
  HwInfo hw;
  hw.expander = g_app->hal->hasExpander();
  hw.adc[0] = g_app->hal->hasAdc(0);
  hw.adc[1] = g_app->hal->hasAdc(1);
  hw.uptime_s = static_cast<uint32_t>(esp_timer_get_time() / 1000000);
  net::Fill(hw);
  return hw;
}

esp_err_t SendJson(httpd_req_t* req, cJSON* j) {
  char* s = cJSON_PrintUnformatted(j);
  cJSON_Delete(j);
  httpd_resp_set_type(req, "application/json");
  httpd_resp_set_hdr(req, "Cache-Control", "no-store");
  esp_err_t r = httpd_resp_sendstr(req, s ? s : "{}");
  cJSON_free(s);
  return r;
}

esp_err_t SendError(httpd_req_t* req, const char* status, const char* msg) {
  httpd_resp_set_status(req, status);
  cJSON* j = cJSON_CreateObject();
  cJSON_AddBoolToObject(j, "ok", false);
  cJSON_AddStringToObject(j, "error", msg);
  return SendJson(req, j);
}

bool Authorized(httpd_req_t* req) {
  if (g_app->net->api_key.empty()) return true;
  char key[65] = "";
  return httpd_req_get_hdr_value_str(req, "X-API-Key", key, sizeof(key)) == ESP_OK && g_app->net->api_key == key;
}

// Reads a JSON body (max 8 KB).
cJSON* ReadBody(httpd_req_t* req) {
  const size_t len = req->content_len;
  if (len == 0 || len > 8192) return nullptr;
  std::string buf(len, '\0');
  size_t got = 0;
  while (got < len) {
    int r = httpd_req_recv(req, buf.data() + got, len - got);
    if (r <= 0) return nullptr;
    got += r;
  }
  return cJSON_Parse(buf.c_str());
}

void SaveRobotConfig() {
  scorbot::RobotConfig copy;
  {
    Locked l;
    copy = *g_app->cfg;
  }
  settings::SaveRobot(copy);
}

cJSON* RunCommand(const cJSON* msg) {
  cJSON* reply = cJSON_CreateObject();
  bool changed = false;
  {
    Locked l;
    api::Command(*g_app->robot, *g_app->cfg, msg, reply, &changed);
  }
  if (changed) SaveRobotConfig();
  return reply;
}

// ---- handlers -------------------------------------------------------------
esp_err_t Index(httpd_req_t* req) {
  httpd_resp_set_type(req, "text/html");
  return httpd_resp_send(req, index_html_start, index_html_end - index_html_start - 1);
}

esp_err_t GetState(httpd_req_t* req) {
  cJSON* j;
  {
    HwInfo hw = Hw();
    Locked l;
    j = api::State(*g_app->robot, *g_app->cfg, hw);
  }
  return SendJson(req, j);
}

esp_err_t PostCmd(httpd_req_t* req) {
  if (!Authorized(req)) return SendError(req, "401 Unauthorized", "bad or missing X-API-Key");
  JsonPtr msg(ReadBody(req), cJSON_Delete);
  if (!msg) return SendError(req, "400 Bad Request", "body must be JSON");
  return SendJson(req, RunCommand(msg.get()));
}

esp_err_t GetConfig(httpd_req_t* req) {
  cJSON* j;
  {
    Locked l;
    j = settings::RobotToJson(*g_app->cfg);
  }
  return SendJson(req, j);
}

esp_err_t PostConfig(httpd_req_t* req) {
  if (!Authorized(req)) return SendError(req, "401 Unauthorized", "bad or missing X-API-Key");
  JsonPtr msg(ReadBody(req), cJSON_Delete);
  if (!msg) return SendError(req, "400 Bad Request", "body must be JSON");
  {
    Locked l;
    settings::RobotFromJson(msg.get(), *g_app->cfg);
  }
  SaveRobotConfig();
  return GetConfig(req);
}

esp_err_t GetWifi(httpd_req_t* req) { return SendJson(req, settings::NetToJson(*g_app->net, false)); }

void RebootSoon(void*) {
  vTaskDelay(pdMS_TO_TICKS(800));
  esp_restart();
}

esp_err_t PostWifi(httpd_req_t* req) {
  if (!Authorized(req)) return SendError(req, "401 Unauthorized", "bad or missing X-API-Key");
  JsonPtr msg(ReadBody(req), cJSON_Delete);
  if (!msg) return SendError(req, "400 Bad Request", "body must be JSON");
  settings::NetFromJson(msg.get(), *g_app->net);
  settings::SaveNet(*g_app->net);
  cJSON* j = cJSON_CreateObject();
  cJSON_AddBoolToObject(j, "ok", true);
  cJSON_AddStringToObject(j, "note", "saved; rebooting to apply");
  SendJson(req, j);
  xTaskCreate(RebootSoon, "reboot", 2048, nullptr, 1, nullptr);
  return ESP_OK;
}

esp_err_t PostReboot(httpd_req_t* req) {
  if (!Authorized(req)) return SendError(req, "401 Unauthorized", "bad or missing X-API-Key");
  {
    Locked l;
    g_app->robot->disable();
  }
  cJSON* j = cJSON_CreateObject();
  cJSON_AddBoolToObject(j, "ok", true);
  SendJson(req, j);
  xTaskCreate(RebootSoon, "reboot", 2048, nullptr, 1, nullptr);
  return ESP_OK;
}

// WebSocket: commands in, {"t":"reply",...} out; state is pushed by Broadcaster.
esp_err_t Ws(httpd_req_t* req) {
  if (req->method == HTTP_GET) return ESP_OK;  // handshake
  httpd_ws_frame_t f = {};
  f.type = HTTPD_WS_TYPE_TEXT;
  if (httpd_ws_recv_frame(req, &f, 0) != ESP_OK) return ESP_FAIL;
  if (f.len == 0 || f.len > 4096) return ESP_OK;
  std::string buf(f.len + 1, '\0');
  f.payload = reinterpret_cast<uint8_t*>(buf.data());
  if (httpd_ws_recv_frame(req, &f, f.len) != ESP_OK) return ESP_FAIL;
  if (f.type != HTTPD_WS_TYPE_TEXT) return ESP_OK;
  JsonPtr msg(cJSON_Parse(buf.c_str()), cJSON_Delete);
  if (!msg) return ESP_OK;

  cJSON* reply;
  const cJSON* key = cJSON_GetObjectItemCaseSensitive(msg.get(), "key");
  if (!g_app->net->api_key.empty() && !(cJSON_IsString(key) && g_app->net->api_key == key->valuestring)) {
    reply = cJSON_CreateObject();
    cJSON_AddBoolToObject(reply, "ok", false);
    cJSON_AddStringToObject(reply, "error", "bad or missing API key");
  } else {
    reply = RunCommand(msg.get());
  }
  cJSON_AddStringToObject(reply, "t", "reply");
  const cJSON* id = cJSON_GetObjectItemCaseSensitive(msg.get(), "id");
  if (cJSON_IsNumber(id)) cJSON_AddNumberToObject(reply, "id", id->valuedouble);
  char* s = cJSON_PrintUnformatted(reply);
  cJSON_Delete(reply);
  httpd_ws_frame_t out = {};
  out.type = HTTPD_WS_TYPE_TEXT;
  out.payload = reinterpret_cast<uint8_t*>(s);
  out.len = std::strlen(s);
  esp_err_t r = httpd_ws_send_frame(req, &out);
  cJSON_free(s);
  return r;
}

// Captive portal: anything unknown goes to the UI.
esp_err_t NotFound(httpd_req_t* req, httpd_err_code_t) {
  httpd_resp_set_status(req, "302 Found");
  httpd_resp_set_hdr(req, "Location", "/");
  return httpd_resp_send(req, nullptr, 0);
}

void Broadcaster(void*) {
  TickType_t last = xTaskGetTickCount();
  for (;;) {
    vTaskDelayUntil(&last, pdMS_TO_TICKS(50));  // 20 Hz
    size_t n = 8;
    int fds[8];
    if (httpd_get_client_list(g_server, &n, fds) != ESP_OK) continue;
    bool any = false;
    for (size_t i = 0; i < n; ++i) any |= httpd_ws_get_fd_info(g_server, fds[i]) == HTTPD_WS_CLIENT_WEBSOCKET;
    if (!any) continue;
    cJSON* j;
    {
      HwInfo hw = Hw();
      Locked l;
      j = api::State(*g_app->robot, *g_app->cfg, hw);
    }
    char* s = cJSON_PrintUnformatted(j);
    cJSON_Delete(j);
    if (!s) continue;
    httpd_ws_frame_t f = {};
    f.type = HTTPD_WS_TYPE_TEXT;
    f.payload = reinterpret_cast<uint8_t*>(s);
    f.len = std::strlen(s);
    for (size_t i = 0; i < n; ++i)
      if (httpd_ws_get_fd_info(g_server, fds[i]) == HTTPD_WS_CLIENT_WEBSOCKET) httpd_ws_send_frame_async(g_server, fds[i], &f);
    cJSON_free(s);
  }
}

void Add(const char* uri, httpd_method_t m, esp_err_t (*h)(httpd_req_t*), bool ws = false) {
  httpd_uri_t u = {};
  u.uri = uri;
  u.method = m;
  u.handler = h;
  u.is_websocket = ws;
  httpd_register_uri_handler(g_server, &u);
}

}  // namespace

namespace web {

void Start(App* app) {
  g_app = app;
  httpd_config_t cfg = HTTPD_DEFAULT_CONFIG();
  cfg.max_uri_handlers = 16;
  cfg.lru_purge_enable = true;
  cfg.stack_size = 8192;
  cfg.core_id = 0;
  cfg.max_open_sockets = 7;
  if (httpd_start(&g_server, &cfg) != ESP_OK) {
    ESP_LOGE(TAG, "HTTP server failed to start");
    return;
  }
  Add("/", HTTP_GET, Index);
  Add("/api/state", HTTP_GET, GetState);
  Add("/api/cmd", HTTP_POST, PostCmd);
  Add("/api/config", HTTP_GET, GetConfig);
  Add("/api/config", HTTP_POST, PostConfig);
  Add("/api/wifi", HTTP_GET, GetWifi);
  Add("/api/wifi", HTTP_POST, PostWifi);
  Add("/api/reboot", HTTP_POST, PostReboot);
  Add("/ws", HTTP_GET, Ws, true);
  httpd_register_err_handler(g_server, HTTPD_404_NOT_FOUND, NotFound);
  xTaskCreatePinnedToCore(Broadcaster, "ws_tx", 6144, nullptr, 4, nullptr, 0);
  ESP_LOGI(TAG, "web UI ready");
}

}  // namespace web
