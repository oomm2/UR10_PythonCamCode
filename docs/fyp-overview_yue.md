# FYP 概覽

[English](fyp-overview.md) ｜ [廣東話](fyp-overview_yue.md)

**文件同步日期：2026-09-23**

## 專案目標

呢個 Final Year Project（FYP）研究用手勢同 Universal Robots UR10 / URSim 互動嘅保守控制介面。專案刻意將 command generation 同 observation 分開，確保 monitoring dashboard 冇能力影響機械人 movement。

## Modules 同責任範圍

| Module | 做咩 | 邊界 |
| --- | --- | --- |
| Vision Controller | 喺本機安全檢查之後，將見到嘅手勢轉做經 filter 嘅 Cartesian velocity request。 | 只有喺 user 明確啟動、而且所有 preconditions 都滿足時，先可以控制 URSim 或機械人。 |
| Read-only Monitor | 顯示 RTDE output telemetry、trends 同本機 3D model。 | 唔可以發送 URScript、設定 RTDE inputs 或出任何 motion command。 |

## 手勢控制流程

1. Camera 擷取一幅 frame。
2. MediaPipe 擷取一隻手嘅 landmarks。
3. App 校準中立手掌位置同大小，之後用 confidence、smoothing、dead-zone 同 confirmation rules 去 filter。
4. 系統只會形成一個方向 request：`LEFT`、`RIGHT`、`UP`、`DOWN`、`FWD`、`BACK`，或者即時 `STOP`。
5. Controller 喺 dispatch RTDE velocity command 之前，會檢查 session identity、control enablement、fresh RTDE feedback、safety state、workspace bounds 同 velocity limits。
6. 如果 monitor 有運行，佢會獨立讀取 output telemetry，只作觀察。

## 示範層級

| 層級 | 範圍 | 預期環境 |
| --- | --- | --- |
| Camera-only | 手勢辨識同 UI feedback，robot control 關閉。 | 只需要本機 camera。 |
| URSim | 喺 simulation 驗證 axes、workspace guards 同 fault behaviour。 | 由 operator 自己設定嘅 local 或另一部機上嘅 URSim。 |
| 實體機械人 | 經過 local commissioning 之後做受監督嘅 controlled validation。 | 已批准嘅 workcell、controller safety settings、risk assessment 同急停。 |

## 安全限制

呢個 implementation 係 prototype，唔係 safety-rated controller。Controller 嘅 software workspace guard 只係 supervisory，而且只會睇 TCP 資料；佢唔可以證明全部 robot links、tooling、payload 或周邊物件都有足夠空間。公開 configuration 入面 `REAL_WORKSPACE_*` 都未設定，所以 physical control 會刻意保持 unavailable。

Monitor 唔係 safety device。佢可以報告 telemetry，但唔可以介入 robot movement。

## 私隱同可重現性

Tracked files 只會有 public-safe examples。真正 address、RTSP credentials、workspace measurements、tool/TCP details、calibration data、screenshots、recordings、logs 同 exports 都要放喺 ignored local files，分享之前一定要審核。

Setup 同 test procedures 請返去睇 [root README](../README_yue.md)。