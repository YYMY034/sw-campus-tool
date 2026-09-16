#!/usr/bin/env python3
"""
运动世界校园 完整登录工具
流程: checkGeeUse → GT4滑块验证 → geevalidate → login
基于 NekoSportsWorldTool 的 iOS 链逆向实现
"""
import base64, hashlib, json, uuid, time, struct, random, string, sys, os
import urllib.request, urllib.parse, urllib.error
from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA
from Crypto.Util.Padding import pad, unpad
from io import BytesIO
from PIL import Image
import numpy as np

# ============================================================
# 常量
# ============================================================
HOST = "https://run.gxapp.iydsj.com"
GEE_HOST = "https://gcaptcha4.geetest.com"
STATIC_HOST = "https://static.geetest.com/"
UA_IOS = "SWCampus/7.3.40 (iPhone; iOS 18.1; Scale/3.00)"
UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

KEYDATA_ONE = "nhang.school"
KEYDATA_TWO = "5K0E8400-E29"
KEYDATA_THREE = "597DEA1AFB49"

CAPTCHA_ID = "8c065103d81f5fd3efec8ad3e3a84c30"

RSA_PUB_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC5pqlTzsGNZk1RxhH4O4x3JNTD
V7FbVH66mPfW5v1tnIy4ty7xv8DGMG4Zn/TvstwlJWeYOADHdi8uF21lJaBvzvPt
VEhifHXZq825fI9hGYtDoaVQmCN/Nfs2dKmt89XDrhtl3SZxO6TumOCTQt+5oqjF
2Jo3o1YtkAyGzjaJnwIDAQAB
-----END PUBLIC KEY-----"""

SALT_ALPHABET = "+kot8A*B45jF6CD@a!UVWubcdKLZ{efgMpNOxyz01PQ}Rn)Tvw23XYh(iG7rsEqJHI9+Slm/"
TOKEN_SIGN_SUFFIX = "2slhe02lsfiwowlcixisla_sls-_slaor"

# GT4 RSA 公钥 (37个28bit limbs)
GT4_LIMBS = [
    134982529, 254232810, 164556709, 234907349, 134685994, 35463984, 258277946, 12518857,
    44638621, 93783641, 212253739, 62792472, 186688352, 109500232, 182488077, 261196188,
    26354094, 103248217, 106891695, 165771045, 41530993, 263704736, 111785174, 12753611,
    232116673, 155524985, 218291229, 122452343, 248250238, 118739550, 251169095, 129059733,
    149835464, 5498868, 71719731, 154456417, 49635,
]

# ============================================================
# fold32 + derive_paes_key
# ============================================================
def c_string_bytes(s):
    b = s.encode('utf-8')
    idx = b.find(b'\x00')
    return b[:idx] if idx >= 0 else b

def fold32(value):
    h = 0
    for c in c_string_bytes(value):
        h = (((h & 0x00FFFFFF) << 8) | c) ^ (h >> 24)
    return h & 0xFFFFFFFF

def ror32(val, bits):
    return ((val >> bits) | (val << (32 - bits))) & 0xFFFFFFFF

def derive_paes_key(one, two, three, four):
    a = fold32(one); b = fold32(two); c = fold32(three); d = fold32(four)
    w9 = c ^ a
    w10 = w9 ^ ror32(w9, 24)
    w9 = w10 ^ ror32(w9, 8)
    w10 = w9 ^ b
    w9 ^= d
    w8 = d ^ b
    w11 = w8 ^ ror32(w8, 24)
    w8 = w11 ^ ror32(w8, 8)
    w11 = w8 ^ a
    w8 ^= c
    w12 = w8 & w10
    w11 ^= w12
    w12 = w8 | w9
    w8 ^= w9
    w10 ^= w12
    w8 = w10 ^ (~w8 & 0xFFFFFFFF)
    w12 = w8 ^ w11
    w8 |= w11
    w8 ^= w10
    w10 = w12 & (~w10 & 0xFFFFFFFF)
    w9 ^= w10
    return struct.pack('<IIII', w9, w8, w12, w11)

# ============================================================
# AES-128-CBC
# ============================================================
def aes_encrypt(key, plaintext, iv=b'\x00'*16):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return cipher.encrypt(pad(plaintext, AES.block_size))

def aes_decrypt(key, ciphertext, iv=b'\x00'*16):
    cipher = AES.new(key, AES.MODE_CBC, iv)
    return unpad(cipher.decrypt(ciphertext), AES.block_size)

def md5_hex(data):
    return hashlib.md5(data).hexdigest()

def b64e(data):
    return base64.b64encode(data).decode()

def b64d(s):
    return base64.b64decode(s)

# ============================================================
# 信封加密
# ============================================================
def random_req_key():
    return ''.join(random.choice(SALT_ALPHABET) for _ in range(16))

def normalize_key_data(value):
    b = value.encode('utf-8')
    if len(b) <= 7:
        missing = 12 - len(b)
        return value + ''.join(random.choices(string.ascii_letters + string.digits, k=missing))
    elif len(b) >= 13:
        return value[-12:]
    return value

class EnvelopeSession:
    def __init__(self):
        now_ms = int(time.time() * 1000)
        # 注意: 必须用 "{:.6f}" (6位小数), 不能写 "{:.6}" (那是6位有效数字,
        # 会输出科学计数法 '1.78944e+12' 导致 keyDataFour 完全错误)
        formatted = "{:.6f}".format(float(now_ms))
        self.key_data_four = normalize_key_data(formatted)
        self.paes_key = derive_paes_key(KEYDATA_ONE, KEYDATA_TWO, KEYDATA_THREE, self.key_data_four)
        self.rsa_key = RSA.import_key(RSA_PUB_PEM)
    
    def build_envelope(self, plaintext, order="insert", ts_ms=None):
        now_ms = ts_ms if ts_ms is not None else int(time.time() * 1000)
        container_data = b64e(plaintext.encode())
        container = json.dumps({
            "data": container_data,
            "timeStamp": now_ms,
            "platform": 1,
            "keyDataOne": KEYDATA_ONE,
            "keyDataTwo": KEYDATA_TWO,
            "keyDataThree": KEYDATA_THREE,
            "keyDataFour": self.key_data_four,
        }, separators=(',', ':'))
        
        req_key = random_req_key()
        d = b64e(aes_encrypt(req_key.encode(), container.encode()))
        h = md5_hex(container.encode())
        cipher_rsa = PKCS1_v1_5.new(self.rsa_key)
        k = b64e(cipher_rsa.encrypt(req_key.encode()))
        
        # ★ 关键: p/t 必须是数字类型(101/0)，字符串 "101" 会被服务端判 11307
        if order == "observed":
            env = json.dumps({"k": k, "p": 101, "d": d, "h": h, "t": 0}, separators=(',', ':'))
        else:
            env = json.dumps({"d": d, "h": h, "k": k, "p": 101, "t": 0}, separators=(',', ':'))
        return env, now_ms

# ============================================================
# iOS 请求头
# ============================================================
def build_ios_header(uid=-1, token="", identity=None):
    """构造 iOS 请求头；identity 传入时复用其稳定 device_id/安装时间/机型。"""
    if identity is not None:
        device_id = identity.device_id or str(uuid.uuid4()).upper()
        install = identity.app_install_time or (int(time.time() * 1000) - 3 * 86400000)
        dev_name = identity.device_name or "iPhone"
        os_ver = identity.os_version or "18.1"
    else:
        device_id = str(uuid.uuid4()).upper()
        install = int(time.time() * 1000) - 3 * 86400000
        dev_name = "iPhone"
        os_ver = "18.1"
    ts = int(time.time() * 1000)
    
    m = {}
    m["osType"] = "1"
    m["DeviceId"] = device_id
    m["deviceName"] = dev_name
    m["CustomDeviceId"] = f"{device_id}_iOS_sportsWorld_campus"
    m["osVersion"] = os_ver
    m["logicPixel"] = "360x640"
    m["physicPixel"] = "1080x1920"
    m["cpuModel"] = "x86_64"
    m["appVersion"] = "7.3.40"
    m["isRoot"] = False
    m["appInstallTime"] = install
    nonce = str(uuid.uuid4()).upper()
    m["nonce"] = nonce
    m["timeStamp"] = ts
    m["studentId"] = uid if uid >= 1 else 0
    if uid >= 1 and token:
        m["uid"] = uid
        m["token"] = token
        query = f"timeStamp={ts}&token={token}&uid={uid}"
        m["tokenSign"] = md5_hex((query + TOKEN_SIGN_SUFFIX).encode())
    
    header_plain = json.dumps(m, separators=(',', ':'))
    extra = {"nonce": nonce, "timeStamp": str(ts)}
    if uid >= 1 and token:
        extra["tokenSign"] = md5_hex(f"timeStamp={ts}&token={token}&uid={uid}{TOKEN_SIGN_SUFFIX}".encode())
    else:
        extra["tokenSign"] = ""
    return header_plain, extra

# ============================================================
# 响应解密
# ============================================================
def decrypt_response(raw_text, session):
    try:
        resp = json.loads(raw_text)
    except:
        return None, raw_text[:200]
    
    if "resp" in resp:
        if isinstance(resp["resp"], str):
            resp = json.loads(resp["resp"])
        elif resp["resp"] is not None:
            resp = resp["resp"]
    
    if not all(k in resp for k in ["r", "s", "v"]):
        return resp, raw_text[:200]
    
    key = session.paes_key
    r_bytes = b64d(resp["r"])
    
    try:
        layer1 = aes_decrypt(key, r_bytes)
    except Exception as e:
        return None, f"第一层AES解密失败: {e}"
    
    layer1_text = layer1.decode('utf-8', errors='replace')
    env = json.loads(layer1_text)
    
    # 如果有 d 字段，再解一层
    if "d" in env:
        try:
            layer2 = aes_decrypt(key, b64d(env["d"]))
            outer = json.loads(layer2)
        except:
            outer = env
    else:
        outer = env
    
    # data 字段 Base64 解码
    if "data" in outer and isinstance(outer["data"], str):
        try:
            business = json.loads(b64d(outer["data"]))
        except:
            business = outer
    else:
        business = outer
    
    return business, None

# ============================================================
# HTTP 工具
# ============================================================
def http_get(url, headers=None):
    req = urllib.request.Request(url, method='GET')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.status, resp.read()

def http_post(url, data, headers=None):
    if isinstance(data, str):
        data = data.encode()
    req = urllib.request.Request(url, data=data, method='POST')
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

# ============================================================
# GT4 滑块验证
# ============================================================
def gee_rsa_public_key():
    """从 28bit limbs 构造 GT4 RSA 公钥"""
    n = 0
    for limb in reversed(GT4_LIMBS):
        n = n * (1 << 28) + limb
    return (n, 65537)

def gt4_rsa_encrypt(plaintext_str):
    """GT4 RSA PKCS1v15 加密 → hex"""
    n, e = gee_rsa_public_key()
    # PKCS1 v1.5 padding
    key_size = (n.bit_length() + 7) // 8
    msg = plaintext_str.encode()
    pad_len = key_size - len(msg) - 3
    em = b'\x00\x02' + bytes([random.randint(1, 255) for _ in range(pad_len)]) + b'\x00' + msg
    m = int.from_bytes(em, 'big')
    c = pow(m, e, n)
    return c.to_bytes(key_size, 'big').hex()

def get_str_16():
    """16个随机hex字符"""
    result = []
    for _ in range(4):
        v = int(65536.0 * (1.0 + random.random()))
        s = format(v, 'x')
        result.append(s[1:5])
    return ''.join(result)

def get_pow_nonce(pow_msg_base):
    """PoW: 找16个hex字符使sha256(base+nonce)以"00"开头"""
    attempts = 0
    while True:
        nonce = ''.join(random.choice('0123456789abcdef') for _ in range(16))
        h = hashlib.sha256((pow_msg_base + nonce).encode()).hexdigest()
        attempts += 1
        if h.startswith("00"):
            print(f"  [GT4] PoW 完成 ({attempts} 次尝试)")
            return nonce

def aes_o(plaintext, str16):
    """GT4 AES-128-CBC(key=str16, IV='0'*16, PKCS7) → hex"""
    ct = aes_encrypt(str16.encode(), plaintext.encode(), iv=b'0'*16)
    return ct.hex()

def jsonp_parse(text):
    start = text.index('(')
    end = text.rindex(')')
    return json.loads(text[start+1:end])

def gt4_slide_distance(bg_png, slice_png):
    """缺口识别：灰度→高斯模糊→完整Canny(NMS+滞回)→全图二维模板匹配。
    对齐参考实现 get_distance_original 的管线（纯 numpy+PIL，向量化）。"""
    from collections import deque
    from numpy.lib.stride_tricks import sliding_window_view

    def load_gray(png):
        arr = np.array(Image.open(BytesIO(png)).convert('RGBA'), dtype=np.float32)
        r, g, b, a = arr[:,:,0], arr[:,:,1], arr[:,:,2], arr[:,:,3]
        r = r * (a / 255.0); g = g * (a / 255.0); b = b * (a / 255.0)
        return (r * 299 + g * 587 + b * 114) / 1000.0

    bg_gray = load_gray(bg_png)
    sl_gray = load_gray(slice_png)

    # 高斯模糊 (5x5, σ≈1.1)
    def gauss_blur(img):
        sigma = 1.1
        k = np.exp(-np.arange(-2, 3)**2 / (2 * sigma * sigma))
        k = k / k.sum()
        tmp = np.apply_along_axis(lambda m: np.convolve(m, k, mode='same'), 1, img)
        return np.apply_along_axis(lambda m: np.convolve(m, k, mode='same'), 0, np.asarray(tmp))

    def canny(img, low=100.0, high=200.0):
        h, w = img.shape
        p = np.pad(img, 1, mode='reflect')
        # 完整 3x3 Sobel
        gy = (-p[0:-2,0:-2] - 2*p[0:-2,1:-1] - p[0:-2,2:]
              + p[2:,0:-2] + 2*p[2:,1:-1] + p[2:,2:])
        gx = (-p[0:-2,0:-2] - 2*p[1:-1,0:-2] - p[2:,0:-2]
              + p[0:-2,2:] + 2*p[1:-1,2:] + p[2:,2:])
        mag = np.hypot(gx, gy)
        ang = (np.degrees(np.arctan2(gy, gx)) % 180) // 45
        # NMS
        nms = np.zeros_like(mag)
        dirs = [(0,-1), (-1,1), (-1,0), (-1,-1)]
        y1, x1 = slice(1, h-1), slice(1, w-1)
        m0 = mag[y1, x1]
        for d, (dy, dx) in enumerate(dirs):
            mask = (ang[y1, x1] == d)
            m1 = np.roll(mag, (-dy, -dx), axis=(0,1))[y1, x1]
            m2 = np.roll(mag, (dy, dx), axis=(0,1))[y1, x1]
            keep = mask & (m0 >= m1) & (m0 >= m2)
            nms[y1, x1][keep] = m0[keep]
        # 双阈值 + 滞回
        strong = nms >= high
        weak = (nms >= low) & (nms < high)
        out = np.zeros((h, w), dtype=np.uint8)
        out[strong] = 255
        dq = deque(zip(*np.where(strong)))
        while dq:
            y, x = dq.popleft()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and weak[ny, nx] and out[ny, nx] == 0:
                        out[ny, nx] = 255
                        dq.append((ny, nx))
        return out.astype(np.float64) * 255.0

    bg_edge = canny(gauss_blur(bg_gray))
    sl_edge = canny(gauss_blur(sl_gray))
    if (sl_edge > 0).sum() < 10:
        return 0  # 切片无边缘特征

    # 全图二维零均值归一化互相关
    bh, bw = bg_edge.shape
    sh, sw = sl_edge.shape
    t = sl_edge - sl_edge.mean()
    tn = np.linalg.norm(t)
    if tn == 0:
        return 0
    win = sliding_window_view(bg_edge, (sh, sw))       # (bh-sh+1, bw-sw+1, sh, sw)
    sv = win - win.mean(axis=(2, 3), keepdims=True)
    num = np.sum(sv * t, axis=(2, 3))
    den = np.sqrt(np.sum(sv**2, axis=(2, 3))) * tn
    with np.errstate(divide='ignore', invalid='ignore'):
        score = np.where(den > 0, num / den, 0)
    best = np.unravel_index(int(np.argmax(score)), score.shape)
    return int(best[1])

def solve_gt4():
    """完整 GT4 滑块求解，返回四凭证。
    缺口识别有概率偏差导致 /verify 拒绝 → 内部重试 3 轮（全新 /load 会话）。"""
    last_err = None
    for attempt in range(1, 4):
        try:
            return _solve_gt4_once()
        except Exception as e:
            last_err = e
            print(f"[GT4] 第 {attempt} 次失败: {str(e)[:120]}")
            if attempt < 3:
                time.sleep(2)
    raise Exception(f"GT4 连续 3 次失败: {last_err}")


def _solve_gt4_once():
    """单轮 GT4 求解"""
    challenge = uuid.uuid4().hex
    callback = f"geetest_{int(time.time()*1000)}"
    
    # 1. /load
    load_url = f"{GEE_HOST}/load?callback={callback}&captcha_id={CAPTCHA_ID}&challenge={challenge}&client_type=web&risk_type=slide&lang=zh"
    print("[GT4] 请求 /load...")
    status, raw = http_get(load_url, {"User-Agent": UA_WEB})
    text = raw.decode()
    data = jsonp_parse(text)["data"]
    
    bg = data["bg"]
    slice_img = data["slice"]
    lot_number = data["lot_number"]
    payload = data["payload"]
    process_token = data["process_token"]
    pow_detail = data["pow_detail"]
    
    print(f"[GT4] lot_number={lot_number}")
    
    # 2. 下载图片
    print("[GT4] 下载滑块图片...")
    _, bg_png = http_get(f"{STATIC_HOST}{bg}", {"User-Agent": UA_WEB})
    _, sl_png = http_get(f"{STATIC_HOST}{slice_img}", {"User-Agent": UA_WEB})
    
    # 3. 识别缺口
    dist = gt4_slide_distance(bg_png, sl_png)
    print(f"[GT4] 缺口距离: {dist}")
    
    # 4. PoW
    pow_base = f"{pow_detail.get('version','')}|{pow_detail.get('bits','')}|{pow_detail.get('hashfunc','')}|{pow_detail.get('datetime','')}|{CAPTCHA_ID}|{lot_number}||"
    pow_nonce = get_pow_nonce(pow_base)
    pow_msg = pow_base + pow_nonce
    pow_sign = hashlib.sha256(pow_msg.encode()).hexdigest()
    
    # 5. get_w
    str16 = get_str_16()
    userresponse = dist / 1.0059466666666665 + 2.0
    
    lf = lambda a, b: lot_number[a:b]
    plaintext = json.dumps({
        "setLeft": dist,
        "passtime": 1887,
        "userresponse": userresponse,
        "device_id": "",
        "lot_number": lot_number,
        "pow_msg": pow_msg,
        "pow_sign": pow_sign,
        "geetest": "captcha",
        "lang": "zh",
        "ep": "123",
        "biht": "1426265548",
        "gee_guard": {"roe": {"aup": "3", "sep": "3", "egp": "3", "auh": "3", "rew": "3", "snh": "3", "res": "3", "cdc": "3"}},
        "YciC": "P3Vn",
        lf(26, 30) + lf(7, 11): lf(6, 14),
        "em": {"ph": 0, "cp": 0, "ek": "11", "wd": 1, "nt": 0, "si": 0, "sc": 0},
    }, separators=(',', ':'))
    
    r_hex = gt4_rsa_encrypt(str16)
    i_hex = aes_o(plaintext, str16)
    w = i_hex + r_hex
    
    # 6. /verify
    cb = f"geetest_{int(time.time()*1000)}"
    verify_url = f"{GEE_HOST}/verify?callback={cb}&captcha_id={CAPTCHA_ID}&client_type=web&lot_number={urllib.parse.quote(lot_number)}&risk_type=slide&payload={urllib.parse.quote(payload)}&process_token={urllib.parse.quote(process_token)}&payload_protocol=1&pt=1&w={urllib.parse.quote(w)}"
    
    print("[GT4] 请求 /verify...")
    status, raw = http_get(verify_url, {"User-Agent": UA_WEB})
    text = raw.decode()
    root = jsonp_parse(text)
    
    if root.get("status") != "success":
        raise Exception(f"GT4 verify 失败: {text[:200]}")
    
    data_obj = root.get("data", {})
    seccode = data_obj.get("seccode")
    if not seccode:
        # 可能 verify 通过但无 seccode（Captcha 接受但不通过）
        raise Exception(f"GT4 verify 无 seccode: {text[:300]}")
    creds = {
        "lotNumber": seccode["lot_number"],
        "captchaOutput": seccode["captcha_output"],
        "passToken": seccode["pass_token"],
        "genTime": seccode["gen_time"],
    }
    print(f"[GT4] 验证成功! lot={creds['lotNumber']}")
    return creds

# ============================================================
# 信封请求发送
# ============================================================
def send_envelope_request(session, method, url, body_plain, extra_headers=None, identity=None):
    """发送信封请求并解密响应；identity 传入时复用其稳定 device_id/安装时间。"""
    # 构造 header
    header_plain, header_extra = build_ios_header(identity=identity)
    header_env, header_ts = session.build_envelope(header_plain, "observed")
    
    # 构造 body (ts = header_ts + 1, 原生两次独立读取毫秒的语义)
    body_env, _ = session.build_envelope(body_plain, "insert", header_ts + 1)
    
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": UA_IOS,
        "appVersion": "7.3.40",
        "headerSign": header_env,
    }
    headers.update(header_extra)
    if extra_headers:
        headers.update(extra_headers)
    
    status, raw = http_post(url, body_env, headers)
    raw_text = raw.decode()
    
    print(f"  HTTP {status} | 响应 {len(raw_text)} 字节")
    
    biz, err = decrypt_response(raw_text, session)
    if err and biz is None:
        print(f"  ❌ 解密失败: {err}")
        return None
    if biz is None:
        print(f"  明文响应: {err}")
        return json.loads(err) if err.startswith('{') else None
    
    print(f"  业务响应: {json.dumps(biz, ensure_ascii=False)[:200]}")
    return biz

# ============================================================
# 完整登录流程
# ============================================================
def login(username, password, identity=None):
    """完整登录链。identity（sw.Identity）传入时复用其稳定 device_id/安装时间。"""
    session = EnvelopeSession()
    print(f"keyDataFour = {session.key_data_four}")
    print(f"pAesKey = {session.paes_key.hex()}")
    if identity is not None:
        print(f"设备身份: platform={identity.platform} device_id={identity.device_id} "
              f"device_name={identity.device_name} os={identity.os_version}")
    
    # Step 1: checkGeeUse
    print("\n" + "="*60)
    print("Step 1: checkGeeUse (免验证码探测)")
    print("="*60)
    body = json.dumps({
        "username": username,
        "uuid": str(uuid.uuid4()),
        "unid": 0,
        "type": 2,
    }, separators=(',', ':'))
    
    biz = send_envelope_request(session, "POST", f"{HOST}/api/v65/security/checkGeeUse",
                                body, identity=identity)
    if biz is None:
        print("❌ checkGeeUse 失败")
        return None
    
    skip = biz.get("data", False)
    print(f"  checkGeeUse data={skip} ({'免验证码' if skip else '需要滑块验证'})")
    
    uuid_value = str(uuid.uuid4())
    
    if not skip:
        # Step 2: GT4 滑块验证
        print("\n" + "="*60)
        print("Step 2: GT4 滑块验证")
        print("="*60)
        creds = solve_gt4()
        
        # Step 3: geevalidate
        print("\n" + "="*60)
        print("Step 3: geevalidate (提交滑块凭证)")
        print("="*60)
        gv_body = json.dumps({
            "lotNumber": creds["lotNumber"],
            "captchaOutput": creds["captchaOutput"],
            "passToken": creds["passToken"],
            "genTime": creds["genTime"],
            "isOffline": False,
            "osType": 1,
            "businessType": 0,
            "uuid": uuid_value,
            "username": username,
        }, separators=(',', ':'))
        
        biz = send_envelope_request(session, "POST", f"{HOST}/api/v70270/security/geevalidate",
                                    gv_body, identity=identity)
        if biz is None:
            print("❌ geevalidate 失败")
            return None
        
        err = biz.get("error")
        if err == 10000:
            print("  ✅ geevalidate 验证通过")
        elif err == 10003:
            print("  ❌ geevalidate 验证失败(10003)")
            return None
        else:
            print(f"  ⚠️ geevalidate error={err}，继续尝试登录")
        
        time.sleep(2)
    
    # Step 4: login
    print("\n" + "="*60)
    print("Step 4: login")
    print("="*60)
    _dev_name = identity.device_name if identity is not None else "iPhone"
    _os_ver = identity.os_version if identity is not None else "18.1"
    login_body = json.dumps({
        "device_model": _dev_name,
        "os_version": _os_ver,
        "mac_address": "",
        "imei": "",
        "loginType": 0,
        "username": username,
        "password": password,
        "uuid": uuid_value,
        "osType": "0",
    }, separators=(',', ':'))
    
    credential = b64e(f"{username}:{password}".encode())
    extra = {"Authorization": f"Basic {credential}"}
    
    biz = send_envelope_request(session, "POST", f"{HOST}/api/v70100/login",
                                login_body, extra, identity=identity)
    if biz is None:
        print("❌ login 失败")
        return None
    
    err = biz.get("error")
    if err == 10000:
        data = biz.get("data", {})
        uid = data.get("uid", 0)
        token = data.get("token", "")
        unid = data.get("unid", "0")
        name = data.get("name", "")
        print(f"\n{'='*60}")
        print(f"✅ 登录成功!")
        print(f"  uid   = {uid}")
        print(f"  token = {token}")
        print(f"  unid  = {unid}")
        print(f"  name  = {name}")
        print(f"{'='*60}")
        return {"uid": uid, "token": token, "unid": unid, "name": name}
    else:
        print(f"❌ 登录失败: error={err} message={biz.get('message','')}")
        print(f"  完整响应: {json.dumps(biz, ensure_ascii=False)}")
        return None

# ============================================================
# MAIN
# ============================================================
if __name__ == '__main__':
    # 依赖检查
    try:
        from PIL import Image
        import numpy as np
    except ImportError:
        print("❌ 需要 Pillow 和 numpy: pip install Pillow numpy")
        sys.exit(1)
    
    # 参数
    if len(sys.argv) >= 3:
        user = sys.argv[1]
        passwd = sys.argv[2]
    else:
        print("\n运动世界校园 - 自动登录工具")
        print("用法: python auto_login.py <手机号> <密码>")
        print("示例: python auto_login.py 13800138000 mypassword123\n")
        user = input("手机号: ").strip()
        passwd = input("密码: ").strip()
    
    print(f"\n开始登录: {user}")
    result = login(user, passwd)
    
    if result:
        # 保存会话
        with open("session.json", "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n会话已保存到 session.json")
