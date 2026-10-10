#!/usr/bin/env python3
"""End-to-end UI test: real web UI + real firmware core on the simulated arm.

    python3 firmware/test/ui_test.py [screenshot_dir]

Needs: sim_server built (see Makefile), pip install aiohttp playwright.
"""
import asyncio
import json
import os
import subprocess
import sys
import time
import urllib.request

from playwright.async_api import async_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = 8099
BASE = f"http://127.0.0.1:{PORT}"
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "screens")
fails = []


def check(cond, what):
    print(("  ok   " if cond else "  FAIL ") + what)
    if not cond:
        fails.append(what)


def post(path, body):
    req = urllib.request.Request(BASE + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req).read())


def state():
    return json.loads(urllib.request.urlopen(BASE + "/api/state").read())


async def wait_for(pred, timeout, step=0.2):
    t0 = time.time()
    while time.time() - t0 < timeout:
        s = state()
        if pred(s):
            return s
        await asyncio.sleep(step)
    return state()


async def main():
    os.makedirs(OUT, exist_ok=True)
    server = subprocess.Popen([sys.executable, os.path.join(HERE, "sim_bridge.py"), str(PORT)], stdout=subprocess.PIPE)
    server.stdout.readline()  # "simulator UI on ..."
    try:
        async with async_playwright() as pw:
            exe = os.environ.get("PW_CHROMIUM")  # e.g. a preinstalled Chromium
            browser = await pw.chromium.launch(executable_path=exe) if exe else await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1400, "height": 900})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            await page.goto(BASE)
            await page.wait_for_function("document.querySelector('#pConn').textContent.includes('connected')", timeout=8000)
            check(True, "UI connects over WebSocket")
            check(not await page.is_visible("#demoBar"), "not in demo mode")

            print("control")
            await page.click("#enableBtn")
            s = await wait_for(lambda s: s["enabled"], 3)
            check(s["enabled"], "Enable button enables the robot")
            before = state()["pos"][0]
            plus = page.locator("#joints .joint").nth(0).locator(".jog button").nth(1)
            box = await plus.bounding_box()
            await page.mouse.move(box["x"] + 10, box["y"] + 10)
            await page.mouse.down()
            await asyncio.sleep(1.2)
            await page.mouse.up()
            await asyncio.sleep(0.5)
            moved = state()["pos"][0] - before
            check(moved > 3, f"holding jog + moves the base ({moved:.1f} deg)")
            mode = (await wait_for(lambda s: s["mode"] == "idle", 2))["mode"]
            check(mode == "idle", "releasing jog stops (mode idle)")
            inp = page.locator("#joints .joint").nth(1).locator("input")
            target = round(state()["pos"][1] + 15, 1)
            await inp.fill(str(target))
            await inp.press("Enter")
            s = await wait_for(lambda s: s["mode"] == "idle" and abs(s["pos"][1] - target) < 1, 10)
            check(abs(s["pos"][1] - target) < 1, f"typed target moves the shoulder to {target} ({s['pos'][1]})")
            await page.screenshot(path=os.path.join(OUT, "control.png"))

            print("test tab")
            await page.click("#tabs [data-tab=test]")
            await page.click("#selfTestBtn")
            s = await wait_for(lambda s: s["mode"] != "test" and all(t["r"] != "pending" for t in s["mtest"]), 20)
            results = [t["r"] for t in s["mtest"]]
            check(results == ["pass"] * 6, f"self-test passes all motors {results}")
            badges = await page.locator("#motorTable .badge.pass").count()
            check(badges == 6, f"6 pass badges shown ({badges})")
            await page.locator("#motorTable tbody tr").nth(2).locator("button", has_text="Flip").click()
            await page.locator("#motorTable tbody tr").nth(2).locator("button", has_text="Test").click()
            await wait_for(lambda s: s["mode"] == "test", 3, 0.05)
            s = await wait_for(lambda s: s["mode"] != "test" and s["mtest"][2]["r"] != "pending", 6)
            check(s["mtest"][2]["r"] == "warn" and "disagree" in s["mtest"][2]["note"], "after Flip, motor test reports disagree")
            await page.locator("#motorTable tbody tr").nth(2).locator("button", has_text="Flip").click()
            await page.screenshot(path=os.path.join(OUT, "test.png"), full_page=True)

            print("calibrate tab")
            await page.click("#tabs [data-tab=calib]")
            await page.locator("#homeBtns button", has_text="Gripper").click()
            s = await wait_for(lambda s: s["homed"][5] and s["mode"] == "idle", 30)
            check(s["homed"][5], "gripper homes (close to stall, then open)")
            await page.locator("#homeBtns button", has_text="Elbow").click()
            s = await wait_for(lambda s: s["homed"][2] and s["mode"] == "idle", 60)
            check(s["homed"][2], "elbow homes on its switch")
            await page.locator("#calBtns button", has_text="Gripper").click()
            s = await wait_for(lambda s: s["mode"] == "idle" and s["cal"][5]["r"] != "pending", 60)
            check(s["cal"][5]["r"] == "pass", f"gripper travel calibration: {s['cal'][5]}")
            await page.click("#applyAllBtn")
            await asyncio.sleep(0.8)
            cpu_cell = await page.locator("#calTable tbody tr").nth(5).locator("td").nth(4).inner_text()
            check("(est)" not in cpu_cell, f"applied value shown as calibrated ({cpu_cell})")
            await page.screenshot(path=os.path.join(OUT, "calibrate.png"), full_page=True)

            print("safety")
            post("/api/sim", {"vm": False})
            await page.wait_for_selector("#faultBar.on", timeout=3000)
            check("E-STOP" in await page.inner_text("#faultBar"), "E-stop banner appears when motor power drops")
            await page.screenshot(path=os.path.join(OUT, "estop.png"))
            post("/api/sim", {"vm": True})
            await page.click("#resetBtn")
            await page.wait_for_selector("#faultBar", state="hidden", timeout=3000)
            check(True, "Clear fault removes the banner")

            print("settings")
            await page.click("#tabs [data-tab=settings]")
            vmax = page.locator("#jointCfg input[data-k=vmax]").nth(0)
            await vmax.fill("33")
            await vmax.dispatch_event("change")
            await page.click("#cfgSave")
            await asyncio.sleep(0.6)
            cfg = json.loads(urllib.request.urlopen(BASE + "/api/config").read())
            check(cfg["joints"][0]["vmax"] == 33, "settings save reaches the controller")
            await page.screenshot(path=os.path.join(OUT, "settings.png"), full_page=True)

            print("demo mode")
            demo = await browser.new_page(viewport={"width": 1400, "height": 900})
            demo.on("pageerror", lambda e: errors.append("demo: " + str(e)))
            await demo.goto(BASE + "/?demo")
            await demo.wait_for_selector("#demoBar.on", timeout=3000)
            await demo.click("#enableBtn")
            await demo.click("#tabs [data-tab=test]")
            await demo.click("#selfTestBtn")
            await demo.wait_for_function("document.querySelectorAll('#motorTable .badge.pass').length===6", timeout=15000)
            check(True, "demo mode runs the self-test")
            await demo.click("#tabs [data-tab=control]")
            await demo.screenshot(path=os.path.join(OUT, "demo.png"))

            mobile = await browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
            await mobile.goto(BASE + "/?demo")
            await mobile.wait_for_timeout(800)
            overflow = await mobile.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            check(not overflow, "no horizontal scroll at phone width")
            await mobile.screenshot(path=os.path.join(OUT, "mobile.png"), full_page=True)

            check(not errors, f"no console errors {errors[:3]}")
            await browser.close()
    finally:
        server.terminate()
    print(f"\n{'ALL PASSED' if not fails else str(len(fails)) + ' FAILED'}; screenshots in {OUT}")
    sys.exit(1 if fails else 0)


asyncio.run(main())
