# coding: utf-8
"""
cache_crypto —— 本地缓存加密（A 档：Windows DPAPI 绑定当前用户）。

用途：
  对 data/cache 下的行情月片 / 面板缓存做"按当前 Windows 用户绑定"的加密。
  密钥由 Windows DPAPI 依据当前登录用户派生，不在磁盘落明文、无需联网。
  文件被复制到其他电脑 / 其他 Windows 用户后将无法解密。

文件格式（加密态）：
  MAGIC(4 字节) + DPAPI 密文(blob)
  明文 pickle 以 '\\x80'（PROTO 标记）开头，与 MAGIC 互斥，借此区分新老格式。

适用平台：Windows。非 Windows 平台调用会抛出 RuntimeError。
"""
import os
import sys
import hmac
import hashlib
import pickle

MAGIC = b'RXCE'
_INNER = b'INNR'

if sys.platform == 'win32':
    import ctypes
    from ctypes import wintypes

    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [('cbData', wintypes.DWORD),
                    ('pbData', ctypes.POINTER(ctypes.c_char))]

    _CRYPTPROTECT_UI_FORBIDDEN = 0x01


def _dpapi_encrypt(plain):
    buf = ctypes.create_string_buffer(plain, len(plain))
    in_blob = _DATA_BLOB(len(plain), ctypes.cast(buf,
                                                ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(cipher):
    buf = ctypes.create_string_buffer(cipher, len(cipher))
    in_blob = _DATA_BLOB(len(cipher), ctypes.cast(buf,
                                                 ctypes.POINTER(ctypes.c_char)))
    out_blob = _DATA_BLOB()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None,
        _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(out_blob))
    if not ok:
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def is_encrypted(raw):
    """判断一段文件原始字节是否为本模块写出的加密格式。"""
    return raw[:len(MAGIC)] == MAGIC


def dumps(obj, protocol=4):
    """对象 -> 加密后的文件字节。

    内层先做完整性信封（SHA-256 摘要），再整体经 DPAPI 加密；
    解密后校验摘要，以弥补 DPAPI 非认证加密、可能漏检密文篡改的问题。
    """
    if sys.platform != 'win32':
        raise RuntimeError('缓存加密仅支持 Windows（DPAPI）')
    plain = pickle.dumps(obj, protocol=protocol)
    digest = hashlib.sha256(plain).digest()
    inner = _INNER + digest + plain
    return MAGIC + _dpapi_encrypt(inner)


def loads(raw):
    """文件原始字节 -> 对象。支持加密格式与历史明文 pickle（自动识别）。"""
    if is_encrypted(raw):
        if sys.platform != 'win32':
            raise RuntimeError('缓存为 Windows DPAPI 加密，当前平台无法解密')
        inner = _dpapi_unprotect(raw[len(MAGIC):])
        if inner[:len(_INNER)] != _INNER:
            raise ValueError('缓存完整性信封缺失或已损坏')
        digest = inner[len(_INNER):len(_INNER) + 32]
        plain = inner[len(_INNER) + 32:]
        actual = hashlib.sha256(plain).digest()
        if not hmac.compare_digest(digest, actual):
            raise ValueError('缓存完整性校验失败（可能被篡改或损坏）')
    else:
        plain = raw
    return pickle.loads(plain)


def save_blob(path, obj, protocol=4):
    """对象加密后原子写入文件。"""
    data = dumps(obj, protocol=protocol)
    tmp = path + '.tmp'
    with open(tmp, 'wb') as f:
        f.write(data)
    os.replace(tmp, path)


def load_blob(path, default=None):
    """读取缓存文件并还原对象；文件不存在返回 default。

    自动识别加密 / 历史明文格式。读到明文缓存时，会用加密格式就地重写，
    完成无感迁移。
    """
    if not os.path.exists(path):
        return default
    with open(path, 'rb') as f:
        raw = f.read()
    obj = loads(raw)
    if not is_encrypted(raw):
        try:
            save_blob(path, obj)
        except OSError:
            pass
    return obj
