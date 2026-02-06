"""
逐觅网站自动签到脚本 - Playwright 浏览器版本
支持拼图滑块验证
"""
import os
import sys
import asyncio
import random
import requests
import base64
import io
from datetime import datetime
from playwright.async_api import async_playwright
from PIL import Image
import pytz

# 修复 Windows 控制台编码问题
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# 滑块缺口识别 API
SLIDER_API_URL = "https://byye.pythonanywhere.com"

# ✅ 配置区 - 建议使用环境变量
USERNAME = os.environ.get("ZHUIMI_USERNAME", "")
PASSWORD = os.environ.get("ZHUIMI_PASSWORD", "")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

BASE_URL = "https://zhuimi.xn--v4q818bf34b.com"
HEADLESS = True  # 设为 False 可以看到浏览器操作过程


def send_telegram(message: str):
    """发送 Telegram 通知"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[通知] 未配置 Telegram Bot，跳过发送。")
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "Markdown"
        }
        response = requests.post(url, json=payload, timeout=10)
        if response.status_code == 200:
            print("[通知] Telegram 消息已发送。")
        else:
            print(f"[通知] Telegram 发送失败，状态码：{response.status_code}")
    except Exception as e:
        print(f"[通知异常] Telegram：{str(e)}")


def compress_base64_image(base64_str: str, max_size_kb: int = 50, quality: int = 85) -> str:
    """
    压缩 base64 编码的图片
    :param base64_str: 原始 base64 图片字符串（可带 data:image/xxx;base64, 前缀）
    :param max_size_kb: 目标最大大小（KB）
    :param quality: JPEG 压缩质量（1-100）
    :return: 压缩后的 base64 字符串（带前缀）
    """
    try:
        # 解析 base64 字符串
        if ',' in base64_str:
            header, data = base64_str.split(',', 1)
        else:
            header = 'data:image/png;base64'
            data = base64_str

        # 解码图片
        img_data = base64.b64decode(data)
        img = Image.open(io.BytesIO(img_data))

        original_size = len(img_data) / 1024
        print(f"[压缩] 原始图片大小: {original_size:.1f}KB, 尺寸: {img.size}")

        # 如果已经足够小，直接返回
        if original_size <= max_size_kb:
            print(f"[压缩] 图片已足够小，无需压缩")
            return base64_str

        # 转换为 RGB（JPEG 不支持透明通道）
        if img.mode in ('RGBA', 'P'):
            # 创建白色背景
            background = Image.new('RGB', img.size, (255, 255, 255))
            if img.mode == 'P':
                img = img.convert('RGBA')
            background.paste(img, mask=img.split()[3] if len(img.split()) == 4 else None)
            img = background
        elif img.mode != 'RGB':
            img = img.convert('RGB')

        # 尝试不同的质量级别进行压缩
        for q in [quality, 70, 50, 30, 20]:
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=q, optimize=True)
            compressed_data = buffer.getvalue()
            compressed_size = len(compressed_data) / 1024

            if compressed_size <= max_size_kb:
                print(f"[压缩] 压缩成功: {compressed_size:.1f}KB (质量={q})")
                compressed_base64 = base64.b64encode(compressed_data).decode('utf-8')
                return f"data:image/jpeg;base64,{compressed_base64}"

        # 如果还是太大，尝试缩小尺寸
        scale = 0.8
        while scale > 0.3:
            new_size = (int(img.size[0] * scale), int(img.size[1] * scale))
            resized_img = img.resize(new_size, Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            resized_img.save(buffer, format='JPEG', quality=50, optimize=True)
            compressed_data = buffer.getvalue()
            compressed_size = len(compressed_data) / 1024

            if compressed_size <= max_size_kb:
                print(f"[压缩] 缩放压缩成功: {compressed_size:.1f}KB (缩放={scale:.1f})")
                compressed_base64 = base64.b64encode(compressed_data).decode('utf-8')
                return f"data:image/jpeg;base64,{compressed_base64}"

            scale -= 0.1

        # 最后的尝试
        print(f"[压缩] 使用最终压缩方案")
        buffer = io.BytesIO()
        final_size = (int(img.size[0] * 0.5), int(img.size[1] * 0.5))
        resized_img = img.resize(final_size, Image.Resampling.LANCZOS)
        resized_img.save(buffer, format='JPEG', quality=30, optimize=True)
        compressed_data = buffer.getvalue()
        compressed_size = len(compressed_data) / 1024
        print(f"[压缩] 最终大小: {compressed_size:.1f}KB")

        compressed_base64 = base64.b64encode(compressed_data).decode('utf-8')
        return f"data:image/jpeg;base64,{compressed_base64}"

    except Exception as e:
        print(f"[压缩] 压缩失败: {e}，返回原图")
        return base64_str


def find_gap_position(bg_base64: str, slider_base64: str) -> int:
    """
    使用远程 API 找到滑块缺口位置
    返回缺口的 x 坐标
    """
    try:
        print("[滑块] 正在调用缺口识别 API...")
        print(f"[滑块] 原始背景图大小: {len(bg_base64)}")
        print(f"[滑块] 原始滑块图大小: {len(slider_base64)}")

        # 压缩图片以避免 413 错误
        compressed_bg = compress_base64_image(bg_base64, max_size_kb=50)
        compressed_slider = compress_base64_image(slider_base64, max_size_kb=30)

        print(f"[滑块] 压缩后背景图大小: {len(compressed_bg)}")
        print(f"[滑块] 压缩后滑块图大小: {len(compressed_slider)}")

        # 调用 API（使用 JSON 格式）
        response = requests.post(
            SLIDER_API_URL,
            json={
                "bg": compressed_bg,
                "front": compressed_slider
            },
            timeout=30
        )

        print(f"[滑块] API 响应状态: {response.status_code}")

        if response.status_code == 200:
            result = response.json()
            print(f"[滑块] API 返回数据: {result}")

            # 解析返回结果：{'code': 0, 'result': x}
            if isinstance(result, dict):
                code = result.get('code', -1)
                if code == 0:
                    gap_x = result.get('result', 0)
                    print(f"[滑块] API 返回缺口位置: x={gap_x}")
                    return gap_x
                else:
                    print(f"[滑块] API 返回错误: {result.get('msg', '未知错误')}")
                    return random.randint(150, 280)
            else:
                print("[滑块] API 返回格式异常")
                return random.randint(150, 280)
        else:
            print(f"[滑块] API 请求失败，状态码: {response.status_code}")
            return random.randint(150, 280)

    except Exception as e:
        print(f"[滑块] API 调用异常: {e}，使用默认偏移")
        return random.randint(150, 280)


def generate_human_track(distance: int) -> list:
    """
    生成模拟人类的滑动轨迹
    返回每一步的 (dx, dy, dt) 列表
    """
    track = []
    current = 0
    mid = distance * 0.7  # 前70%加速
    t = 0.2
    v = 0

    while current < distance:
        if current < mid:
            # 加速阶段
            a = random.uniform(2, 4)
        else:
            # 减速阶段
            a = random.uniform(-3, -1)

        v0 = v
        v = v0 + a * t
        move = v0 * t + 0.5 * a * t * t
        move = max(1, int(move))

        if current + move > distance:
            move = distance - current

        # 添加 Y 轴微小抖动
        dy = random.uniform(-1, 1) if random.random() > 0.7 else 0

        # 时间间隔（毫秒）
        dt = random.randint(10, 30)

        track.append((move, dy, dt))
        current += move

    # 添加一些微小的回调，模拟人类修正
    if random.random() > 0.5:
        back = random.randint(1, 3)
        track.append((-back, 0, random.randint(50, 150)))
        track.append((back, 0, random.randint(30, 80)))

    return track


async def solve_slider_captcha(page) -> bool:
    """
    解决滑块验证码
    流程：等待滑块出现 -> 获取图片 -> 调用API计算距离 -> 模拟拖动
    """
    try:
        print("[滑块] 等待滑块验证码出现...")

        # 等待滑块手柄出现
        slider_element = None
        try:
            slider_element = await page.wait_for_selector('#sliderHandle', timeout=5000)
            if slider_element:
                print("[滑块] 找到滑块元素: #sliderHandle")
        except:
            print("[滑块] 未找到滑块元素，可能不需要验证或已签到")
            return True

        if not slider_element:
            return True

        # 等待一下让图片加载完成
        await asyncio.sleep(0.5)

        # 截图保存当前状态
        await page.screenshot(path='slider_captcha.png')
        print("[滑块] 已保存滑块截图: slider_captcha.png")

        # 获取背景图和滑块图
        bg_base64 = None
        slider_base64 = None

        # 根据 slide.html 结构获取图片
        # 背景图: .slider-captcha-bg
        # 滑块图: #sliderPuzzle img
        try:
            bg_element = await page.query_selector('.slider-captcha-bg')
            if bg_element:
                src = await bg_element.get_attribute('src')
                if src and 'data:image' in src:
                    bg_base64 = src
                    print("[滑块] 获取到背景图 (.slider-captcha-bg)")
                    print(bg_base64)
        except Exception as e:
            print(f"[滑块] 获取背景图失败: {e}")

        try:
            slider_img = await page.query_selector('#sliderPuzzle img')
            if slider_img:
                src = await slider_img.get_attribute('src')
                if src and 'data:image' in src:
                    slider_base64 = src
                    print("[滑块] 获取到滑块图 (#sliderPuzzle img)")
                    print(slider_base64)
        except Exception as e:
            print(f"[滑块] 获取滑块图失败: {e}")

        # 备用方法: 从页面 JavaScript 变量获取
        if not bg_base64 or not slider_base64:
            try:
                captcha_data = await page.evaluate('''() => {
                    // 尝试从各种可能的变量获取
                    if (window.captchaData) return window.captchaData;
                    if (window.__captcha__) return window.__captcha__;
                    if (window.sliderCaptcha) return window.sliderCaptcha;

                    // 尝试从所有 img 标签获取 base64 图片
                    const imgs = document.querySelectorAll('img[src^="data:image"]');
                    if (imgs.length >= 2) {
                        return {
                            backgroundImage: imgs[0].src,
                            sliderImage: imgs[1].src
                        };
                    }
                    return null;
                }''')
                if captcha_data:
                    if not bg_base64:
                        bg_base64 = captcha_data.get('backgroundImage')
                    if not slider_base64:
                        slider_base64 = captcha_data.get('sliderImage')
                    if bg_base64:
                        print("[滑块] 从 JS 变量获取到背景图")
                    if slider_base64:
                        print("[滑块] 从 JS 变量获取到滑块图")
            except Exception as e:
                print(f"[滑块] 从 JS 获取图片失败: {e}")

        # 计算滑动距离
        if bg_base64 and slider_base64:
            gap_x = find_gap_position(bg_base64, slider_base64)

            # 获取滑块拼图的初始位置（通常在左侧）
            slider_puzzle = await page.query_selector('#sliderPuzzle')
            slider_initial_x = 0
            if slider_puzzle:
                puzzle_box = await slider_puzzle.bounding_box()
                if puzzle_box:
                    slider_initial_x = puzzle_box['x']
                    print(f"[滑块] 滑块拼图初始位置: x={slider_initial_x}")

            # 获取背景图的位置和宽度，用于计算比例
            bg_element = await page.query_selector('.slider-captcha-bg')
            scale_factor = 1.0
            bg_offset_x = 0
            if bg_element:
                bg_box = await bg_element.bounding_box()
                if bg_box:
                    bg_offset_x = bg_box['x']
                    # 假设原图宽度为 340（常见值），计算缩放比例
                    actual_width = bg_box['width']
                    print(f"[滑块] 背景图实际宽度: {actual_width}, 位置: x={bg_offset_x}")
                    # API 返回的是基于原图的坐标，需要根据实际显示大小调整
                    if actual_width > 0:
                        scale_factor = actual_width / 340  # 340 是常见的原图宽度

            # 计算实际需要滑动的距离
            # gap_x 是缺口在原图中的 x 坐标
            # 需要转换为实际页面上的滑动距离
            distance = int(gap_x * scale_factor)

            # 减去滑块图片本身的宽度偏移（滑块图片通常有一定宽度）
            slider_img = await page.query_selector('#sliderPuzzle img')
            if slider_img:
                img_box = await slider_img.bounding_box()
                if img_box:
                    # 滑块图片的中心应该对准缺口中心
                    slider_img_width = img_box['width']
                    print(f"[滑块] 滑块图片宽度: {slider_img_width}")
                    # 通常需要减去滑块图片宽度的一半或一定偏移
                    distance = distance - int(slider_img_width * 0.6)

            print(f"[滑块] API返回缺口位置: {gap_x}, 缩放比例: {scale_factor:.2f}, 最终滑动距离: {distance}")
        else:
            print("[滑块] 无法获取验证码图片，使用默认距离")
            distance = random.randint(150, 280)

        print(f"[滑块] 计算滑动距离: {distance}")

        # 获取滑块位置
        box = await slider_element.bounding_box()
        if not box:
            print("[滑块] 无法获取滑块位置")
            return False

        start_x = box['x'] + box['width'] / 2
        start_y = box['y'] + box['height'] / 2

        print(f"[滑块] 滑块起始位置: ({start_x}, {start_y})")

        # 生成滑动轨迹
        track = generate_human_track(distance)
        print(f"[滑块] 生成轨迹点数: {len(track)}")

        # 执行滑动
        await page.mouse.move(start_x, start_y)
        await asyncio.sleep(random.uniform(0.1, 0.3))

        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.05, 0.1))

        current_x = start_x
        current_y = start_y

        for dx, dy, dt in track:
            current_x += dx
            current_y += dy
            await page.mouse.move(current_x, current_y)
            await asyncio.sleep(dt / 1000)  # 转换为秒

        await asyncio.sleep(random.uniform(0.1, 0.3))
        await page.mouse.up()

        print("[滑块] 滑动完成，等待验证结果...")
        await asyncio.sleep(1.5)

        # 截图保存滑动后状态
        await page.screenshot(path='slider_after.png')
        print("[滑块] 已保存滑动后截图: slider_after.png")

        # 检查是否验证成功
        # 如果滑块消失，说明验证成功
        try:
            still_visible = await page.query_selector('#sliderHandle')
            if not still_visible:
                print("[滑块] ✅ 验证成功（滑块已消失）")
                return True
        except:
            pass

        # 检查页面是否有成功提示
        page_content = await page.content()
        if '验证成功' in page_content or '签到成功' in page_content:
            print("[滑块] ✅ 验证成功")
            return True

        print("[滑块] 验证状态未知，继续执行")
        return True

    except Exception as e:
        print(f"[滑块] 处理异常: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def main():
    """主函数"""
    beijing_tz = pytz.timezone('Asia/Shanghai')
    now = datetime.now(beijing_tz).strftime("%Y-%m-%d %H:%M:%S")

    api_link = "未知"
    expire_time_str = "未知"
    remaining_days = "未知"
    sign_msg = ""
    today_sign_count = "未知"
    continuous_days = "未知"

    async with async_playwright() as p:
        # 启动浏览器
        print("[浏览器] 正在启动...")
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=['--disable-blink-features=AutomationControlled']
        )

        context = await browser.new_context(
            viewport={'width': 1280, 'height': 800},
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        )

        # 注入反检测脚本
        await context.add_init_script('''
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
        ''')

        page = await context.new_page()

        try:
            # ========== 登录 ==========
            print("[登录] 正在打开登录页面...")
            await page.goto(f"{BASE_URL}/user/login", wait_until='networkidle')
            await asyncio.sleep(1)

            # 填写用户名密码
            print("[登录] 填写登录信息...")
            await page.fill('input[name="username"]', USERNAME)
            await page.fill('input[name="password"]', PASSWORD)

            # 处理验证码输入（如果有）
            captcha_input = await page.query_selector('input[name="login_token"]')
            if captcha_input:
                await captcha_input.fill("小满")

            # 点击登录按钮
            await page.click('button[type="submit"]')
            await asyncio.sleep(2)

            # 检查是否登录成功
            current_url = page.url
            if 'dashboard' in current_url or 'login' not in current_url:
                print("[登录] ✅ 登录成功！")
            else:
                print("[登录] ⚠️ 可能登录失败，继续尝试...")
                await page.screenshot(path='login_result.png')

            # ========== 获取用户信息 ==========
            print("[信息] 正在获取用户信息...")
            await page.goto(f"{BASE_URL}/dashboard", wait_until='networkidle')
            await asyncio.sleep(1)

            # 保存 dashboard 截图用于调试
            await page.screenshot(path='dashboard_page.png')
            print("[调试] 已保存 dashboard 页面截图: dashboard_page.png")

            # 使用 JavaScript 获取用户信息（更可靠的方式）
            user_info = await page.evaluate('''() => {
                const result = {
                    apiLink: null,
                    expireTime: null,
                    debug: []
                };

                // 获取 API 链接 - 多种选择器尝试
                const apiSelectors = [
                    '#tvboxLinkContainer .endpoint-url code',
                    '.endpoint-url code',
                    '#tvboxLinkContainer code',
                    '.api-link code',
                    'code[class*="endpoint"]',
                    '.card-body code'
                ];

                for (const selector of apiSelectors) {
                    const el = document.querySelector(selector);
                    if (el && el.innerText.trim()) {
                        result.apiLink = el.innerText.trim();
                        result.debug.push(`API链接选择器命中: ${selector}`);
                        break;
                    }
                }

                // 如果还没找到，尝试从所有 code 标签中查找包含 http 的
                if (!result.apiLink) {
                    const allCodes = document.querySelectorAll('code');
                    for (const code of allCodes) {
                        const text = code.innerText.trim();
                        if (text.includes('http') && text.includes('/')) {
                            result.apiLink = text;
                            result.debug.push(`从 code 标签找到 API 链接`);
                            break;
                        }
                    }
                }

                // 获取到期时间 - 多种选择器尝试
                const expireSelectors = [
                    '.expire-time',
                    '.expiry-time',
                    '.expire-date',
                    '[class*="expire"]',
                    '.subscription-expire',
                    '.vip-expire'
                ];

                for (const selector of expireSelectors) {
                    const el = document.querySelector(selector);
                    if (el && el.innerText.trim()) {
                        result.expireTime = el.innerText.trim();
                        result.debug.push(`到期时间选择器命中: ${selector}`);
                        break;
                    }
                }

                // 如果还没找到，尝试从页面文本中匹配日期格式
                if (!result.expireTime) {
                    const bodyText = document.body.innerText;
                    // 匹配 YYYY-MM-DD HH:MM:SS 格式
                    const dateMatch = bodyText.match(/(\\d{4}-\\d{2}-\\d{2}\\s+\\d{2}:\\d{2}:\\d{2})/);
                    if (dateMatch) {
                        result.expireTime = dateMatch[1];
                        result.debug.push(`从页面文本匹配到日期: ${dateMatch[1]}`);
                    }
                }

                // 调试：列出页面上的关键元素
                result.debug.push(`页面标题: ${document.title}`);
                const cards = document.querySelectorAll('.card, .panel, .box');
                result.debug.push(`找到 ${cards.length} 个卡片/面板元素`);

                return result;
            }''')

            # 打印调试信息
            if user_info.get('debug'):
                for debug_msg in user_info['debug']:
                    print(f"[调试] {debug_msg}")

            # 获取 API 链接
            if user_info.get('apiLink'):
                api_link = user_info['apiLink']
                print(f"[信息] API链接: {api_link}")
            else:
                print("[信息] 未找到 API 链接")

            # 获取到期时间
            if user_info.get('expireTime'):
                expire_time_str = user_info['expireTime']
                print(f"[信息] 到期时间: {expire_time_str}")

                # 计算剩余天数
                try:
                    expire_time = datetime.strptime(expire_time_str, "%Y-%m-%d %H:%M:%S")
                    expire_time = expire_time.replace(tzinfo=beijing_tz)
                    remaining_days = (expire_time - datetime.now(beijing_tz)).days + 1
                    print(f"[信息] 剩余天数: {remaining_days}")
                except Exception as e:
                    print(f"[信息] 计算剩余天数失败: {e}")
            else:
                print("[信息] 未找到到期时间")

            # ========== 签到 ==========
            print("[签到] 正在打开签到页面...")
            await page.goto(f"{BASE_URL}/signin", wait_until='networkidle')
            await asyncio.sleep(1)

            # 截图查看页面状态
            await page.screenshot(path='signin_page.png')
            print("[调试] 已保存签到页面截图: signin_page.png")

            # 尝试多次验证
            max_attempts = 3
            for attempt in range(max_attempts):
                print(f"[签到] 第 {attempt + 1}/{max_attempts} 次尝试...")

                # 1. 先点击签到按钮，触发滑块验证
                sign_btn = await page.query_selector('#signinButton')
                if sign_btn:
                    await sign_btn.click()
                    print("[签到] 点击签到按钮，等待滑块验证弹出...")
                    await asyncio.sleep(1)
                else:
                    print("[签到] 未找到签到按钮")
                    break

                # 2. 等待滑块出现并处理验证
                slider_success = await solve_slider_captcha(page)

                if not slider_success:
                    print("[签到] 滑块验证失败，重试...")
                    await page.reload()
                    await asyncio.sleep(1)
                    continue

                await asyncio.sleep(2)

                # 3. 检查签到结果
                # 通过 .signin-action-title 判断签到状态
                try:
                    action_title = await page.query_selector('.signin-action-title')
                    if action_title:
                        title_text = await action_title.inner_text()
                        if '今日已签到' in title_text:
                            sign_msg = "🎉 签到成功！"
                            print("[签到] ✅ 检测到签到成功标识")
                            break
                except Exception as e:
                    print(f"[签到] 检查签到状态失败: {e}")

                # 备用检查方式
                page_content = await page.content()
                if '签到成功' in page_content or '今日已签到' in page_content:
                    sign_msg = "🎉 签到成功！"
                    break
                elif '已签到' in page_content or '已经签到' in page_content:
                    sign_msg = "ℹ️ 今日已签到"
                    break
                else:
                    if attempt < max_attempts - 1:
                        print("[签到] 未检测到成功，重试...")
                        await page.reload()
                        await asyncio.sleep(1)

            if not sign_msg:
                sign_msg = "⚠️ 签到状态未知，请手动检查"

            # ========== 获取签到统计信息 ==========
            print("[签到] 正在获取签到统计信息...")
            try:
                # 刷新页面以获取最新数据
                await page.goto(f"{BASE_URL}/signin", wait_until='networkidle')
                await asyncio.sleep(1)

                # 使用 JavaScript 获取签到统计信息（更可靠的方式）
                signin_stats = await page.evaluate('''() => {
                    const result = {
                        todayCount: null,
                        continuousDays: null,
                        debug: []
                    };

                    // 方法1: 尝试从 .signed-info-compact 结构获取
                    const infoItems = document.querySelectorAll('.signed-info-compact .signed-info-item');
                    result.debug.push(`找到 ${infoItems.length} 个 signed-info-item 元素`);

                    infoItems.forEach((item, index) => {
                        const label = item.querySelector('.info-label');
                        const value = item.querySelector('.info-value');
                        if (label && value) {
                            const labelText = label.innerText.trim();
                            const valueText = value.innerText.trim();
                            result.debug.push(`Item ${index}: ${labelText} = ${valueText}`);

                            if (labelText.includes('今日') || labelText.includes('次数')) {
                                result.todayCount = valueText;
                            }
                            if (labelText.includes('连续') || labelText.includes('天数')) {
                                result.continuousDays = valueText;
                            }
                        }
                    });

                    // 方法2: 尝试从其他可能的结构获取
                    if (!result.todayCount || !result.continuousDays) {
                        const allInfoValues = document.querySelectorAll('.info-value');
                        result.debug.push(`找到 ${allInfoValues.length} 个 info-value 元素`);

                        allInfoValues.forEach((el, index) => {
                            const parent = el.parentElement;
                            if (parent) {
                                const labelEl = parent.querySelector('.info-label');
                                if (labelEl) {
                                    const labelText = labelEl.innerText.trim();
                                    const valueText = el.innerText.trim();
                                    result.debug.push(`InfoValue ${index}: ${labelText} = ${valueText}`);

                                    if (!result.todayCount && (labelText.includes('今日') || labelText.includes('次数'))) {
                                        result.todayCount = valueText;
                                    }
                                    if (!result.continuousDays && (labelText.includes('连续') || labelText.includes('天数'))) {
                                        result.continuousDays = valueText;
                                    }
                                }
                            }
                        });
                    }

                    // 方法3: 尝试从页面文本中提取
                    if (!result.continuousDays) {
                        const bodyText = document.body.innerText;
                        const continuousMatch = bodyText.match(/连续[签到]*[：:]*\\s*(\\d+)\\s*天?/);
                        if (continuousMatch) {
                            result.continuousDays = continuousMatch[1];
                            result.debug.push(`从页面文本匹配到连续签到: ${continuousMatch[1]}`);
                        }
                    }

                    return result;
                }''')

                # 打印调试信息
                if signin_stats.get('debug'):
                    for debug_msg in signin_stats['debug']:
                        print(f"[调试] {debug_msg}")

                # 获取今日签到次数
                if signin_stats.get('todayCount'):
                    today_sign_count = signin_stats['todayCount']
                    print(f"[签到] 今日签到次数: {today_sign_count}")
                else:
                    print("[签到] 未找到今日签到次数")

                # 获取连续签到天数
                if signin_stats.get('continuousDays'):
                    continuous_days = signin_stats['continuousDays']
                    print(f"[签到] 连续签到天数: {continuous_days}")
                else:
                    print("[签到] 未找到连续签到天数")

            except Exception as e:
                print(f"[签到] 获取签到统计信息异常: {e}")
                import traceback
                traceback.print_exc()

            # 保存最终截图
            await page.screenshot(path='signin_result.png')
            print("[调试] 已保存结果截图: signin_result.png")

        except Exception as e:
            sign_msg = f"❌ 执行异常: {str(e)}"
            print(f"[错误] {str(e)}")
            import traceback
            traceback.print_exc()
            await page.screenshot(path='error_screenshot.png')

        finally:
            await browser.close()

    # 整合消息并发送
    telegram_msg = f"""📅 *逐觅签到通知*

👤 用户名：{USERNAME}
🔗 专属链接：{api_link}
📆 到期时间：{expire_time_str}
📊 剩余天数：{remaining_days} 天

{sign_msg}
📈 今日签到次数：{today_sign_count}
🔥 连续签到天数：{continuous_days}
🕒 时间：{now}
"""

    print("\n" + "=" * 50)
    print(telegram_msg)
    print("=" * 50)
    send_telegram(telegram_msg)


if __name__ == "__main__":
    asyncio.run(main())
