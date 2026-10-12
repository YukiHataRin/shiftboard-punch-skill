# shiftboard-punch

讓 Claude Code 在幫你工作時，自動把工作時間登錄到 [Shiftboard](https://shiftboard.fclinlab.com) 工時系統的 skill。

- **專案開始時**：agent 先問你這個專案屬於哪個 Shiftboard 計畫。
- **每項工作開始時**：自動開始計時。
- **每項工作完成時**：agent 寫一段詳細的工作摘要，連同時段一起打卡到 Shiftboard。

兩項工作之間等你回覆的空檔不會被算進工時。

## 需求

- [Claude Code](https://claude.com/claude-code)
- Python 3.9 以上（macOS 內建的 `python3` 即可，不需要安裝任何套件）
- 一個 Shiftboard 帳號，且已被管理員加入至少一個計畫

## 安裝

把這個 repo clone 到 Claude Code 的個人 skill 資料夾：

```bash
mkdir -p ~/.claude/skills
git clone https://github.com/YukiHataRin/shiftboard-punch-skill.git ~/.claude/skills/shiftboard-punch
```

> 資料夾名稱必須是 `shiftboard-punch`。之後要更新版本，在該資料夾執行 `git pull` 即可。

## 設定帳號密碼

```bash
cd ~/.claude/skills/shiftboard-punch
cp config.example.json config.json
chmod 600 config.json
```

用編輯器打開 `config.json`，填入自己的 Shiftboard 帳號密碼：

```json
{
  "api_base_url": "https://api.shiftboard.fclinlab.com",
  "username": "你的帳號",
  "password": "你的密碼",
  "require_confirmation": false
}
```

| 欄位 | 說明 |
| --- | --- |
| `api_base_url` | Shiftboard API 位址，一般不需要修改 |
| `username` / `password` | 你的 Shiftboard 登入帳密 |
| `require_confirmation` | `false`：工作完成後自動打卡；`true`：每次打卡前先給你確認內容 |

確認設定是否正確：

```bash
python3 ~/.claude/skills/shiftboard-punch/scripts/shiftboard.py check
```

看到「登入成功」就完成了。

### 關於密碼安全

- `config.json` 已列在 `.gitignore`，不會被提交到 git。
- `chmod 600` 讓只有你自己的帳號能讀取這個檔案。
- skill 規定 agent 不得讀取或顯示 `config.json`，也不會要求你在對話中提供密碼；帳密只由腳本在登入時使用。
- 密碼以明文存在本機檔案中，請勿在共用電腦上使用，也不要把 `config.json` 傳給別人。

## 使用方式

安裝並設定好之後，照常使用 Claude Code 即可：

1. 在專案中開始新的對話並交辦工作時，agent 會先列出你參與的計畫，請你選擇這次的工作屬於哪一個（同一個專案資料夾下次會預設沿用）。
2. 每項工作開始時 agent 會開始計時；完成時會寫好摘要並打卡，在回覆最後告訴你登錄的計畫與時段，例如：

   ```text
   已打卡：計畫 X，14:05–15:20（1 小時 15 分）
   ```

3. 想換計畫時直接告訴 agent，例如「接下來的工作改記到 Y 計畫」。

計畫與計時狀態是依「專案資料夾」分開記錄的，所以同時在不同專案工作也不會混在一起。

### 打卡摘要格式

每筆工時的說明會寫成一段詳細摘要，依序包含：

| 標籤 | 內容 |
| --- | --- |
| 【主題】 | 專案名稱與這項工作的一句話標題（打卡格子只顯示開頭，所以放最前面） |
| 【完成內容】 | 具體完成的 2～5 件事 |
| 【影響範圍】 | 主要修改的檔案、模組、API 或服務 |
| 【驗證】 | 如何確認結果，以及結果 |
| 【後續】 | 未完成項目、待確認事項或風險 |

範例：

```text
【主題】shiftboard：管理員打卡模式切換 【完成內容】新增「管理模式／個人模式」切換開關，管理員可在個人模式使用班表、課表與工作打卡；後端 GET /time-entries/ 新增 mine 參數，個人打卡頁只顯示自己的紀錄；管理端工時總覽納入管理員時數 【影響範圍】frontend/src/layouts/MainLayout.jsx、AuthContext.jsx、ScheduleGrid.jsx、backend/routers/time_entries.py 【驗證】本機以測試帳號完成打卡、排班與工時總覽檢查，桌面與手機版畫面正常；部署後確認正式站版本已更新 【後續】無
```

摘要只會寫實際完成的事，並且不會包含密碼、token、金鑰、內部 IP 或個資。

## 手動指令

平常由 agent 自動操作，你也可以自己執行（請在專案資料夾內執行）：

```bash
SB="python3 $HOME/.claude/skills/shiftboard-punch/scripts/shiftboard.py"
```

| 指令 | 用途 |
| --- | --- |
| `$SB check` | 確認設定與登入 |
| `$SB projects` | 列出可登錄的計畫 |
| `$SB start --project <id> --note "說明"` | 選定此資料夾的計畫並開始計時 |
| `$SB start --note "說明"` | 沿用此資料夾的計畫開始計時 |
| `$SB status` | 查看此資料夾的計畫與計時中的工作 |
| `$SB log --summary "摘要"` | 把計時中的工作打卡（加 `--dry-run` 只預覽） |
| `$SB log --summary "摘要" --start 09:00 --end 10:30 [--date 2026-10-11]` | 補登過去的時段 |
| `$SB entries [--date 2026-10-11]` | 列出自己某天的工時紀錄 |
| `$SB stop` | 停止計時，不打卡 |
| `$SB forget` | 清除此資料夾記住的計畫 |

## 安全設計

- **不會刪除或覆蓋紀錄**：打卡前會先檢查你自己當天的紀錄，時段重疊就不送出，並列出重疊的紀錄讓你決定新時段。此工具沒有刪除功能。
- **防止誤登長時段**：單筆超過 12 小時會被拒絕（通常是忘記打卡），請確認實際時間後分段補登。
- **只能登錄被指派的計畫**：沒有被加入的計畫會被拒絕。
- **相容新舊版 Shiftboard**：紀錄一律依自己的使用者 id 過濾，管理員帳號也不會看到或誤判別人的紀錄。

## 疑難排解

| 狀況 | 處理方式 |
| --- | --- |
| 找不到設定檔 / 仍是範例內容 | 依「設定帳號密碼」建立並填寫 `config.json` |
| 登入失敗 | 確認帳密可在 Shiftboard 網頁登入 |
| 目前沒有可登錄工時的計畫 | 請 Shiftboard 管理員把你加入計畫 |
| 此時段與既有紀錄重疊 | 用 `$SB entries` 查看當天紀錄，再用 `--start` / `--end` 指定不重疊的時段 |
| 計時已超過 12 小時 | 上次可能忘記打卡：用 `$SB stop` 停止後，以 `--start` / `--end` 補登實際時段 |
| agent 沒有主動問計畫 | 直接跟 agent 說「用 shiftboard-punch 打卡」，或在對話開頭提醒它 |

## 檔案結構

```text
shiftboard-punch/
├── SKILL.md              # 給 agent 的使用說明（何時問計畫、何時打卡、摘要格式）
├── scripts/shiftboard.py # 呼叫 Shiftboard API 的指令工具（只用 Python 標準函式庫）
├── config.example.json   # 設定檔範本
├── config.json           # 你的帳密（自行建立，不會進 git）
└── state/                # 各專案資料夾的計畫與計時狀態（自動產生，不會進 git）
```
