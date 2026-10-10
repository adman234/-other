# Host tests for the portable robot core (no ESP-IDF needed).
CXX ?= g++
CXXFLAGS = -std=c++17 -O2 -Wall -Wextra -I../main
SRC = test_core.cpp ../main/core/robot.cpp ../main/core/config.cpp

test_core: $(SRC) sim_hal.h ../main/core/robot.h ../main/core/config.h
	$(CXX) $(CXXFLAGS) $(SRC) -o $@

# Real-time simulator used by sim_bridge.py. cJSON: point CJSON at a checkout
# of https://github.com/DaveGamble/cJSON (ESP-IDF bundles it as "json").
CJSON ?= cJSON
SIM_SRC = sim_server.cpp ../main/api.cpp ../main/settings.cpp ../main/core/robot.cpp ../main/core/config.cpp

sim_server: $(SIM_SRC) sim_hal.h ../main/api.h ../main/settings.h ../main/core/robot.h
	$(CXX) $(CXXFLAGS) -DSETTINGS_HOST_TEST -I$(CJSON) $(SIM_SRC) $(CJSON)/cJSON.c -o $@

run: test_core
	./test_core

clean:
	rm -f test_core sim_server
.PHONY: run clean
