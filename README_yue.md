# UR10 FYP 視覺控制與監察套件

[English](README.md) ｜ [廣東話](README_yue.md)

**文件同步日期：2026-09-23**

呢個係一個重視私隱嘅 Final Year Project（FYP）套件，適用於 Universal Robots UR10 / URSim。佢有兩個刻意分開嘅 modules：

| Module | 用途 | 可唔可以控制機械人 |
| --- | --- | --- |
| [Vision Controller](#vision-controller) | Qt dashboard 用 MediaPipe 手勢產生保守嘅 Cartesian velocity commands。 | **可以，但一定要手動啟動。** 實體控制預設 fail-closed。 |
| [Read-only Monitor](#read-only-monitor) | 喺本機睇 RTDE output telemetry，同埋 3D UR10 dashboard。 | **唔可以。** 冇 RTDE input recipe、URScript 或 motion-command path。 |

> **學術 prototype，唔係經認證嘅安全系統。** 請先測試 camera-only 行為，再用 URSim。兩個 modules 都唔可以取代現場風險評估、controller safety configuration、protective stops 或實體急停。

## FYP 概覽

呢個 FYP 研究用手勢同機械人互動，同時將控制路徑同觀察路徑分開：

```text
Camera → MediaPipe landmarks → 校準同手勢 filter → safety gate → RTDE controller
                                                      ↘
                                                       optional read-only monitor → 本機 browser dashboard
```

Controller 先會負責出 command；monitor 只會讀 RTDE output telemetry。Monitor 可以報告異常狀態，但唔可以阻止、停止或者移動機械人。

- [FYP overview and scope](docs/fyp-overview.md)
- [廣東話 FYP 概覽](docs/fyp-overview_yue.md)
- [連線同 commissioning 指引](CONNECTION_GUIDE.md)
- [Security 同私隱政策](SECURITY.md)

## Vision Controller

主要支援嘅 controller app 係 `ur10_vision_qt_app.py`。`ur10_vision_app.py` 只係 compatibility launcher，唔係主 implementation。

### 功能

- PySide6 dashboard，有 camera preview 同單手 MediaPipe tracking
- 中立姿態校準、信心值過濾、平滑化同方向手勢分區
- RTDE control 路徑會喺獨立 worker process 運行，避免 native connection failure 令 Qt UI 一齊出事
- URSim 專用示範工作空間同保守速度預設
- 實體 profile 預設冇 workspace limits，所以未喺本機量度同審核前，physical control 會保持鎖定
- Safety-policy unit tests

### Camera-only 快速開始

呢個 mode 唔需要機械人或者 network connection。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python ur10_vision_qt_app.py
```

1. 撳 **開啟相機**；有需要就揀 camera source `0`。
2. 張開手掌、保持唔郁，撳 **校準中立姿態**。
3. 睇畫面上嘅方向狀態，同時保持 robot control 關閉。

macOS 安裝 `ur-rtde` 時可能需要 Boost：

```bash
brew install boost
export CMAKE_PREFIX_PATH="$(brew --prefix boost):${CMAKE_PREFIX_PATH:-}"
python -m pip install --no-cache-dir ur-rtde
```

### 本機 controller 設定

改設定前，先複製公開安全嘅樣板：

```bash
cp settings.example.json settings.json
```

`settings.json` 已經被 Git ignore。真正 robot IP、RTSP URL、credentials、camera settings、calibration 同個人調校值，只可以放喺呢個本機檔案。Commit 入面嘅全部都只係 example：

- URSim：`127.0.0.1`
- 實體機械人地址：留空
- Camera：`0`
- RTSP format：`rtsp://<username>:<password>@<camera-host>:554/stream`

## Read-only Monitor

`monitor/` 有一個獨立、Windows-oriented 嘅本機 telemetry dashboard。佢只會讀 RTDE **outputs**，提供 browser dashboard、trends、可選嘅本機 recordings，同埋 3D UR10 model。佢唔會發送 URScript、設定 RTDE inputs，亦唔會出任何 robot motion command。

### Monitor 快速開始

```powershell
cd monitor
Copy-Item config.example.json config.json
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe server.py
```

打開 `http://127.0.0.1:8080`。除非經過明確審核嘅 trusted local network deployment，否則 HTTP server 應該一直留喺 loopback。真正 controller address 同 heartbeat token 只可以放喺 ignored 嘅 `monitor/config.json` 同 `monitor/.env`。

Monitor API、recording、heartbeat 同 dashboard 詳情請睇 [monitor/README.md](monitor/README.md)。Recordings、screenshots、exported reports 同 telemetry 有機會包含 deployment 或個人資料，分享之前要自己審核。

## 安全同資料處理

- 做任何 hardware test 前，先測試 controller 嘅 camera-only mode 同 URSim。
- `safety_config.py` 入面所有 `REAL_WORKSPACE_*` 都係 `None`，所以公開版會刻意鎖定 physical control。
- Software workspace check 只係睇 TCP 行為，唔可以取代 controller safety functions，亦唔會涵蓋全部 links、tool、payload 或 collision hazards。
- 唔好 commit 真實 IP、RTSP URL、password、certificate、workspace measurements、tool/TCP data、logs、camera captures、recordings 或 assistant-session artifacts。
- Monitor 只係觀察工具，唔係 safety device。佢嘅 read endpoints 絕對唔可以直接 exposed 去 internet。

## 測試

### Vision controller

```bash
python -m unittest discover -s tests -v
```

### Read-only monitor

```powershell
cd monitor
Copy-Item config.example.json config.json
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test "tests/js/*.test.mjs"
```

Monitor tests 同 smoke test 只會用本機 loopback，唔會開 robot connection。CI 會分開跑 controller 同 monitor checks。

## Repo 結構

```text
ur10_vision_qt_app.py  主要支援嘅 Qt vision controller
safety_config.py       Controller safety profiles 同 velocity validation
settings.example.json  公開安全嘅 controller configuration template
monitor/               Read-only RTDE monitor、dashboard、model assets 同 tests
docs/                  英文同廣東話 FYP overview
tests/                 Controller safety-policy tests
```

## License 同 third-party material

Root [MIT License](LICENSE) 適用於 root controller project 同文件，除非有更指定嘅 notice。整合入嚟嘅 monitor 原始 source code **唔會**因為 root MIT 而被重新授權；請睇 [monitor/LICENSE.md](monitor/LICENSE.md)。佢 bundle 咗嘅 Three.js、urdf-loader 同 Universal Robots model assets 會繼續跟隨各自嘅 notices，喺 [monitor/THIRD_PARTY_NOTICES.md](monitor/THIRD_PARTY_NOTICES.md)。
