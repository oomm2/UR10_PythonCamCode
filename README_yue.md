# UR10 Vision Control

[English](README.md) ｜ [廣東話](README_yue.md)

呢個係一個用 Python / Qt 寫嘅桌面 prototype。佢會用 MediaPipe 偵測單隻手嘅 landmarks，經過校準、穩定化同安全檢查之後，透過 RTDE 向 Universal Robots URSim 或 UR10 產生保守嘅 Cartesian velocity command。

> **只係 prototype，唔係經認證嘅安全系統。** 請先做純相機示範或者 URSim 測試，唔好用喺有人喺附近嘅自動化操作。真實機械人測試一定要有現場風險評估、控制器安全設定、已驗證嘅工作空間、實體急停，同埋合資格人士看管。

## 有咩功能

- PySide6 dashboard，有相機預覽同單手 MediaPipe tracking
- 中立手勢校準、信心值過濾、平滑化同手勢分區
- RTDE 控制會喺獨立 worker process 運行，避免 native reconnect 問題令介面一齊中斷
- URSim 專用示範工作空間同速度限制
- 實體 UR10 預設 fail-closed：未喺本機量度同審核工作空間之前，控制會保持鎖定
- Safety policy unit tests

## 快速開始：只用相機示範

呢個 mode 唔需要機械人，亦唔需要網絡連線。

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python ur10_vision_qt_app.py
```

1. 撳 **開啟相機**；有需要就揀 camera source `0`。
2. 張開手掌、保持唔郁，撳 **校準中立姿態**。
3. 睇畫面上顯示嘅方向狀態；呢個步驟請保持機械人控制關閉。

macOS 安裝 `ur-rtde` 時可能需要 Boost：

```bash
brew install boost
export CMAKE_PREFIX_PATH="$(brew --prefix boost):${CMAKE_PREFIX_PATH:-}"
python -m pip install --no-cache-dir ur-rtde
```

## 本機設定

repo 入面只會有 [settings.example.json](settings.example.json) 呢份公開安全嘅樣板。要改設定前，請喺自己部機複製一份：

```bash
cp settings.example.json settings.json
```

`settings.json` 已經被 Git ignore。真實機械人地址、RTSP URL、帳密、相機 source，同埋個人調校值，只可以放喺本機嘅呢個檔案，**千祈唔好 commit**。

公開版用嘅全部都係 generic example：

- URSim：`127.0.0.1`
- 實體機械人 IP：留空
- 相機：`0`
- RTSP example：`rtsp://<username>:<password>@<camera-host>:554/stream`

## 可選：URSim 示範

當你喺自己本機設定好 URSim，並且手動啟動控制之後，程式先可以連線。詳情請睇 [CONNECTION_GUIDE.md](CONNECTION_GUIDE.md)。Source 入面有一組只畀 URSim 用嘅示範工作空間；真係啟動任何 movement 前，請先喺你自己嘅 simulator 逐個方向同所有邊界驗證一次。

## 實體 UR10 安全

公開 `safety_config.py` 入面所有 `REAL_WORKSPACE_*` 預設都係 `None`，所以實體控制會刻意保持鎖定。唔好公開已量度嘅工作空間範圍、TCP／tool 資料、控制器地址、logs、screenshots，或者 production configuration。所有本機量度同設定都應該留喺 version control 之外。

呢個程式嘅軟件工作空間 guard 唔可以取代 Universal Robots Safety Planes、protective stops、controller limits、實體急停，或者正式風險評估。

## 測試

```bash
python -m unittest discover -s tests -v
```

## 專案結構

```text
ur10_vision_qt_app.py  Qt 介面、相機處理、手勢邏輯、RTDE client
ur10_vision_app.py     Qt app 嘅兼容 launcher
safety_config.py       安全 profiles 同速度驗證
settings.example.json  公開安全嘅設定樣板
tests/                 safety-policy unit tests
```

## 授權

呢個專案使用 [MIT License](LICENSE)。
