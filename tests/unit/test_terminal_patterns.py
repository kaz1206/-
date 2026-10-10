"""Log patterns checked against lines copied from the hardware terminal log (2026-10-10, build 6230 -> 6251)."""

import codecs

from robustlab.mt5.terminal import (
    BUILD_RE,
    START_PATTERN,
    SUCCESS_PATTERN,
    UPDATE_RE,
    decode_log,
)

REAL = (
    "IK\t0\t20:03:55.546\tStartup\tsuccessfully initialized from start config \"C:\\work\\job.ini\"\r\n"
    "QP\t0\t20:03:55.670\tLiveUpdate\tstart \"C:\\Users\\<user>\\AppData\\Roaming\\MetaQuotes\\Terminal\\X\\"
    "liveupdate\\terminal64.exe\" /update /path:\"C:\\MT5_verify\" /portable /config:\"C:\\work\\job.ini\"\r\n"
    "EG\t0\t20:03:55.803\tTerminal\texit with code 0\r\n"
    "MR\t0\t20:04:10.592\tTerminal\tMetaTrader 5 x64 build 6251 started for MetaQuotes Ltd.\r\n"
    "KG\t0\t20:04:13.275\tTester\tautomatic testing started\r\n"
    "LP\t0\t20:04:25.255\tTester\tlast test passed with result \"successfully finished\" in 0:00:05.631\r\n"
)
OLD_BUILD = "EH\t0\t19:54:58.992\tTerminal\tMetaTrader 5 x64 build 6230 started for MetaQuotes Ltd.\r\n"
NOT_UPDATE = "RF\t0\t19:55:00.822\tLiveUpdate\tnew version build 6251 (IDE: 6251, Tester: 6251) is available\r\n"


def test_patterns_match_real_lines():
    text = decode_log(codecs.BOM_UTF16_LE + (OLD_BUILD + REAL).encode("utf-16-le"))
    assert UPDATE_RE.search(text)
    assert BUILD_RE.findall(text) == ["6230", "6251"]
    assert START_PATTERN in text and SUCCESS_PATTERN in text


def test_update_available_or_download_lines_are_not_a_handoff():
    assert not UPDATE_RE.search(NOT_UPDATE)
    assert not UPDATE_RE.search("LP\t0\t19:55:39.683\tLiveUpdate\t'mt5clw64' downloaded (68087 kb)\r\n")
