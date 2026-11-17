# Assignment #4 — MQTT 實習與設計

**題目：Secure Orchid IoT Control Platform（六場域蘭花溫室 MQTT 監控與控制）**

> 本報告以課堂作業為讀者。每一節都會標示 **【老師要求】** 與 **【工程延伸】**，
> 讓評分者能清楚區分「題目要求的部分」與「為了讓系統更可靠、更安全而額外做的部分」。
> 所有數值門檻（溫濕度範圍、變化率、遲滯帶等）皆為 **lab assumption（實驗室假設值）**，
> 不是真實感測器規格，也不是蘭花栽培標準。

---

## 1. 題目與目的

**【老師要求】**
- 使用 MQTT 建立 IoT 監控系統，練習 Broker 安裝、帳號密碼、TLS、Publish/Subscribe。
- 目標溫度 **20°C**、目標濕度 **60%**。
- 溫度過高 → **同時開啟兩支風扇**；濕度過高 → **同時開啟兩台除濕機**。

**【工程延伸】**
目的不只是「MQTT 有跑」，而是讓整條鏈路都可被驗證：

```
收到 → 身分驗證 → 授權 → 資料驗證 → 新鮮度 → 合理性 → 決策 → 群組指令 → ACK → 實際狀態 → 稽核
```

並且滿足兩個原則：
1. **中央斷線 ≠ 溫室失控**（各場域在地自主控制）。
2. **感測資料、指令、日誌都是不可信輸入**，必須各自通過信任關卡。

## 2. 系統情境

六個蘭花溫室（site01–site06），每個溫室有：

| 設備 | 數量 | 說明 |
|---|---|---|
| 溫度感測器 temp01 | 1 | 每秒回報（模擬器） |
| 濕度感測器 humidity01 | 1 | 每秒回報（模擬器） |
| 風扇 fan01 / fan02 | 2 | 由場域控制器的繼電器輸出驅動，具回授（模擬） |
| 除濕機 dehumidifier01 / 02 | 2 | 同上 |
| 場域 Broker | 1 | Mosquitto 2.1.2，只服務本場域 |
| 場域控制器 | 1 | 驗證、規則、仲裁、群組致動、ACK |

中央有：中央 Broker、Historian（歷史資料 + 稽核）、Central API、Dashboard、Reverse Proxy。

## 3. MQTT 架構

```
瀏覽器 ──HTTPS 127.0.0.1:8443──▶ Reverse Proxy ──(DMZ)──▶ Dashboard / Central API
Central API、Historian ──MQTT5+TLS──▶ 中央 Broker              （Central OT 網段）
中央 Broker ◀══ bridge01_net … bridge06_net ══▶ site01…06 Broker   （每場域一條獨立橋接網段）
siteNN Broker ◀──MQTT5+TLS──▶ siteNN 控制器、siteNN 感測模擬器      （每場域獨立 LAN）
```

**【老師要求】** Broker 與 Pub/Sub。
**【工程延伸】**
- Docker 中**真的分開 15 個網段**（wan / dmz / central_ot / 6 條 bridge / 6 個 site LAN），
  除 `wan_net` 外全部 `internal`。主機只開放 `127.0.0.1:8443`，**沒有對主機開放任何 MQTT port**。
- 場域 Broker 透過 **MQTT 5 Bridge（TLS 驗證中央憑證）** 連到中央，每個場域有獨立身分
  `bridge-site01` … `bridge-site06`，而且只橋接明確列出的 topic，**絕不橋接 `#`**。

## 4. Topic Design

採 `orchid/v1/{site}/...` 階層：

| Topic | 方向 | QoS | Retain |
|---|---|---|---|
| `orchid/v1/site01/sensor/temp01/telemetry/temperature` | 感測器 → 控制器（及中央） | 0 | 否 |
| `orchid/v1/site01/gateway/telemetry/temp01/temperature` | 控制器 → 中央（附驗證結果） | 1 | 否 |
| `orchid/v1/site01/actuator/fan01/state` | 控制器 → 中央 | 1 | **是** |
| `orchid/v1/site01/control/fan_group/command` | 中央 → 場域 | 1 | 否（**含 Message Expiry**） |
| `orchid/v1/site01/control/fan_group/ack` | 場域 → 中央 | 1 | 否 |
| `orchid/v1/site01/control/dehumidifier_group/command` / `ack` | 同上 | 1 | 否 |
| `orchid/v1/site01/gateway/heartbeat` | 控制器 → 中央（含 LWT） | 1 | **是** |
| `orchid/v1/site01/alerts`、`/events` | 控制器 → 中央 | 1 | 否 |

設計重點：指令對象是 **群組**（`fan_group`），不是兩個獨立的 `fan01 ON`、`fan02 ON`，
因為老師的語意是「同時開兩支」。完整契約見 `docs/MQTT_CONTRACT.md`。

## 5. Broker 安裝

**【老師要求】** 安裝並啟動 Broker。

- 使用官方映像 `eclipse-mosquitto:2.1.2-alpine`，以 Docker Compose 建 7 個 Broker（1 中央 + 6 場域）。
- 自訂 entrypoint：把 secret 以 0600 權限複製到容器私有目錄，再啟動 Mosquitto。
- 資源限制明確設定（不使用 unlimited）：`max_packet_size 16384`、`max_queued_messages`、
  `max_queued_bytes`、`max_inflight_messages 20`、`max_keepalive 120`、`max_qos 1`、`max_connections`。
- 實作中發現的版本差異：Mosquitto 2.1 若把 `max_qos` 這類 listener 層級設定寫在任何
  `listener` 之前，會**自動多開一個 1883 明文 listener**。已改寫設定並以測試確認 1883 被拒絕。

## 6. Username / Password

**【老師要求】** 帳號密碼認證，禁止匿名。

**【工程延伸】**
- `allow_anonymous false`，使用 **Dynamic Security plugin**，預設 ACL 四項全部 deny：
  `publishClientSend / publishClientReceive / subscribe / unsubscribe = deny`，再逐一授權。
- 每個裝置、控制器、橋接、中央服務都有**獨立帳號**（共 27 個），密碼由
  `scripts/bootstrap_secrets.py` 隨機產生，以 PBKDF2-SHA512 雜湊存放，**從不印出、從不 commit**。
- 每個帳號**綁定固定 client id**，避免偷到一組密碼就能把別人的連線踢掉（session takeover）。
- 範例 ACL：`site01-temp01` 只能 publish 自己的溫度 topic；`bridge-site01` 只能 publish
  site01 已登錄設備的 topic，不能碰 site02。

## 7. TLS

**【老師要求】** 使用 TLS 加密 MQTT。

- 以 Python `cryptography` 建立 lab CA，替中央 Broker、6 個場域 Broker、Reverse Proxy 產生含正確
  **SAN** 的憑證（`scripts/generate_certs.py`，跨平台）。
- 所有 MQTT 連線 port 8883、TLS 1.2 以上（實測協商為 TLS 1.3），客戶端
  `CERT_REQUIRED + check_hostname`；Bridge 使用 `bridge_cafile`、`bridge_insecure false`。
- **全專案沒有任何 `--insecure` / `tls_insecure_set(true)`**。測試中以錯誤主機名稱連線會被拒絕。

## 8. Publish / Subscribe

**【老師要求】** 完成 Pub/Sub。

- 感測器（publisher）每秒發布遙測 → 場域控制器（subscriber）訂閱、驗證、決策。
- 控制器發布狀態、ACK、心跳、事件 → 經 Bridge 送到中央 → Historian 訂閱並存檔。
- 中央 API 發布群組指令（QoS 1 + MQTT 5 Message Expiry）→ 經 Bridge 到場域控制器。
- 心跳與實際狀態使用 **retained** 訊息；控制器設定 **LWT（retained OFFLINE）**，連線後發布 ONLINE。

## 9. 六場域監控

Dashboard（React，HTTPS）有三個畫面：

1. **總覽**：六張場域卡片，顯示溫度、濕度、四台致動器、場域狀態、資料品質、最後更新時間、
   警報數、模式。顏色：**綠**=正常、**琥珀**=警告/降級、**紅**=嚴重/離線/控制失敗、**灰**=過期/未知。
2. **場域細節**：即時數值、10 分鐘趨勢圖、感測品質統計、指令紀錄（含 ACK 結果）、警報、Gateway 狀態；
   operator 角色才看得到控制面板。
3. **系統/安全**：連線場域數、Broker 狀態、認證失敗、被拒事件、限流數、指令成功/失敗、**稽核鏈完整性**。

重要細節：**retained 的 `ON` 不等於「目前 ON」**。只有在 observed_at 新鮮、心跳新鮮、LWT 顯示在線時
才顯示 ON；否則顯示灰色 `STALE (last: ON)`。

畫面截圖：`evidence/generated/ui/`（Playwright 實際操作真實系統所截）。

## 10. 控制邏輯

**【老師要求】**
- 溫度高 → `fan_group` ON（fan01 + fan02）
- 濕度高 → `dehumidifier_group` ON（dehumidifier01 + dehumidifier02）

**【工程延伸】**
- **遲滯（hysteresis）**：溫度 ≥ 20.5°C 開、≤ 19.5°C 關；濕度 ≥ 62% 開、≤ 58% 關，避免在 20°C 附近反覆開關。
- **N-of-M 去抖動**：最近 5 筆「已驗證」資料中有 3 筆超標才動作，單一尖峰不會驅動設備。
- **群組致動**：同一步驟驅動兩個輸出 → 讀回實際狀態 → 彙整成 ACK：
  `APPLIED`（兩支都成功）／`PARTIAL_FAILURE`（只成功一支，場域變 DEGRADED 並發出警報）／`FAILED`。
- **控制權狀態機**，優先序：
  `HARD_SAFETY_INTERLOCK（≥32°C 強制開風扇）> FAILSAFE（感測資料失效）> MAINTENANCE > MANUAL_OVERRIDE > AUTO`。
  手動覆寫必須有 operator、reason、created_at、expires_at，**TTL 到期自動回 AUTO**，所有模式切換都進稽核。
  遠端指令**無法繞過**安全連鎖（例如過熱時遠端關風扇會被拒絕）。

## 11. 安全性設計（重點摘要）

| 面向 | 做法 |
|---|---|
| 感測資料 | RAW → 結構/型別/有限數/單位/身分 → NORMALIZED → 範圍/序號/新鮮度/變化率/凍結 → VALIDATED；只有 VALIDATED 能進控制 |
| 過期指令 | MQTT 5 Message Expiry + 應用層 `expires_at` 雙重防護；session 有上限；重送以 command_id 去重 |
| 日誌 | 視為不可信輸入：大小/深度限制、事件類型白名單、CR/LF/控制字元中和、身分/嚴重度由伺服器決定、去重、限流 |
| 稽核 | `HMAC(前一筆 || 本筆)` 串鏈，金鑰不在資料庫內；**可偵測竄改，但不等於防止竄改** |
| Web | JWT（15 分鐘）、viewer/operator 角色、嚴格 CSP、React 只以文字呈現裝置資料 |
| Secrets | 全部由 bootstrap 產生、gitignored；掃描 repo、git 歷史、證據、容器日誌皆無洩漏 |

詳見 `docs/SECURITY_MODEL.md`。

## 12. 測試與結果

驗證方式：`python scripts/verify_all.py --fresh` 一鍵完成「設定檢查 → 單元測試 → 前端建置/測試 →
Docker 建置 → 等待 23 個服務健康 → 實機整合/安全/故障注入測試 → Playwright 瀏覽器測試 →
稽核鏈驗證 → secret 掃描 → 驗收矩陣」。

最終 canonical fresh verification 已在 GitHub Actions 的乾淨 Linux runner 上完成：

| 項目 | 最終結果 |
|---|---|
| 核心驗收 | **34 / 34 PASS** |
| FAIL | **0** |
| INCONCLUSIVE | **0** |
| Browser QA | **PASS** |
| Unit tests | PASS |
| Frontend tests / production build | PASS |
| 23-service health | PASS |
| Integration / security / failure stack tests | PASS |
| Audit-chain verification | PASS |
| Secret scan | **CLEAN** |

先前唯一的 G34 blocker（空白 reason 的 INVALID ACK 沒帶回原 command_id）已在 commit `0836663` 修正，
並由補充驗證 G19b 與完整 fresh run 確認通過。最終結果以 `docs/TEST_MATRIX.md` 與
`docs/FINAL_ACCEPTANCE_REPORT.md` 為準。

此外，同一套 Docker stack 已於 Seeed Studio / NVIDIA Tegra **ARM64** Linux 主機實際部署，
23 個服務全數 healthy，`/api/health` 回傳 `{"status":"ok","mqtt_connected":true}`。
ARM64 主機上的 Linux bind-mount 權限差異以 POSIX ACL 處理，未降低 secret 的 host-side 保護。

## 13. 攻擊 / 異常防呆（實際做過的注入）

| 情境 | 注入方式 | 系統反應 |
|---|---|---|
| 錯誤資料 | `"20abc"`、`null`、`NaN`、`Infinity`、華氏單位、畸形 JSON、過大/過深 JSON、偽造 site_id、重放序號 | 全部被拒（各有明確結果碼），即使值為 30°C 風扇也不動 |
| 過期/未來時間戳 | device_ts 前後偏移 120 秒 | STALE / TIMESTAMP_FUTURE，不進控制 |
| 尖峰 | 單筆 +15°C、200°C | SPIKE_PENDING / RANGE_FAIL，不致動 |
| 設備故障 | fan02 卡在 OFF | PARTIAL_FAILURE、CRITICAL 警報、場域 DEGRADED |
| 中央斷線 | 停止中央 Broker；切斷 site03 的 bridge 網段 | 場域照常自動控制；恢復後補傳（標記 late） |
| 日誌系統掛掉 | 停止 Historian、注入本地日誌寫入失敗 | 控制不受影響 |
| 過期指令 | 控制器離線時送出指令，過期後再上線 | 不執行（Broker 丟棄或回 EXPIRED） |
| 日誌注入 | CR/LF + 偽造 JSON 行 + ANSI 跳脫碼 | 只存一筆，控制字元以可見跳脫保存 |
| XSS | `<script>alert(1)</script>` | API 回傳純文字；瀏覽器顯示為文字，沒有執行 |
| 冒充身分 | 事件宣稱 `principal=admin`、`severity=INFO` | 被丟棄並記錄，由伺服器決定身分與嚴重度 |
| 洪水攻擊 | 3000 筆低優先事件 + 5 筆關鍵事件 | 低優先被限流，5 筆關鍵全保存，磁碟有上限 |
| 稽核竄改 | 刪除 trigger 後修改/刪除一筆稽核資料 | 驗證程式回報 FAIL 並指出位置 |
| 超大封包 | 64 KiB MQTT 訊息 | 連線被中斷，Broker 不當機 |

## 14. 限制

- 感測器與致動器為模擬；門檻值為實驗室假設值。
- 中央只能辨識到「哪一個場域的 Bridge」送來的訊息，無法用密碼學證明是哪一台設備（需端對端簽章）。
- Mosquitto 2.1.2 會執行 ACL 拒絕但不在 info 等級記錄；Dashboard 顯示的是認證失敗與應用層拒絕。
- 單機 SQLite；稽核鏈是「可偵測竄改」而非「不可竄改」。
- Lab CA 預設不在作業系統信任清單中，瀏覽器需匯入 `secrets/pki/ca.crt`。
- 完整清單見 `docs/SECURITY_MODEL.md` 第 10 節與 `docs/FINAL_ACCEPTANCE_REPORT.md`。

## 15. 結論

本作業完成老師要求的 MQTT Broker 安裝、帳號密碼、TLS、Pub/Sub，以及「溫度高開兩支風扇、
濕度高開兩台除濕機」的控制邏輯；並在此基礎上把系統做成可重現（一鍵 bootstrap + 一鍵驗證）、
可測試（單元 / 整合 / 安全 / 故障注入 / 瀏覽器）、可視覺化、且每個驗收項目都有實際執行證據的 local lab。

最重要的收穫是：MQTT「能通」只是起點；真正的系統需要回答「這筆資料可不可信」、
「這個指令是否已過期」、「畫面上的 ON 是不是現在的 ON」、「中央掛掉時溫室會怎樣」這些問題，
而這些答案都必須用測試與證據來證明，而不是用口頭宣稱。
