"""P0 界面校验：导航收敛 + 题库内嵌页真的把本地题库装进 iframe。"""
import os
import sys

os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH",
                      r"C:\Users\Administrator\Documents\tiku-studio\runtime\ms-playwright")

from playwright.sync_api import sync_playwright

URL = "http://127.0.0.1:8100/#/bank"
OUT = r"C:\Users\Administrator\Documents\tiku-studio\data\p0-bank.png"

errors: list[str] = []

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(URL, wait_until="domcontentloaded")
    page.wait_for_selector("iframe.frame, .offline", timeout=20000)
    page.wait_for_timeout(2500)

    nav = [t.strip() for t in page.locator("nav.nav a").all_inner_texts()]
    nav = [t for t in nav if t]
    frame_urls = [f.url for f in page.frames]
    has_iframe = page.locator("iframe.frame").count() > 0
    bar = page.inner_text(".bar") if page.locator(".bar").count() else ""
    page.screenshot(path=OUT, full_page=False)
    browser.close()

print("nav:", nav)
print("frames:", frame_urls)
print("iframe:", has_iframe)
print("bar:", " ".join(bar.split()))
print("console errors:", errors[:5])
ok = (has_iframe
      and any("8080" in u for u in frame_urls)
      and len(nav) == 8
      and not any("错题集" in n or "整卷导入" in n for n in nav))
print("PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
