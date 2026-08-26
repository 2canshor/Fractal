# Apple Principles × Fractal Continuous Improvement

這層不是另一套 Fractal，也不是新的 lifecycle。它只把 Apple 的設計原則變成
`System Review` 的可驗收條件。Fractal 的核心仍然是 `Continuous Improvement`：
工作先由既有 `Perspective` 取得現實證據，再由既有 `System Review` 判斷系統要保留、
修正或移除甚麼。Apple alignment 不會繞過這條路，更不會自行授權 activation、
publication 或任何 persistent change。

## 來源邊界

Registry 以本機 Apple Guide Catalogue 的 `INDEX.json` version 2（更新日期
2026-08-21）為清單真相，完整保留當中 171 個官方 HIG page：

- 每個 page 都有穩定 `source_id`、Apple-relative `source_label`、官方 Apple URL 及
  所選文字檔的 SHA-256；Registry 不保存機器專屬 absolute path。
- 143 個 page 使用現有較新的 ` (2).txt`，另外 28 個只有 indexed file，全部均有
  明確 `source_variant`，不會靜默猜測替代版本。
- `source_manifest_sha256` 綁定全部 171 筆 source record；增刪、改名、改 URL、改 hash
  或重新分類都會 fail closed。
- `validate_apple_principles_registry(..., source_root=...)` 可以再逐一讀取 171 個本機檔案，
  驗證實際 bytes 是否仍然符合 retained SHA-256。

171 個 page 的 current applicability 分成三類：12 個 `universal`、109 個
`conditional`、50 個 `not-current`。這不是把 159 個 page 丟掉，而是把判斷變成可稽核：

- `universal` 對每一項 persistent responsibility 都適用，必須有 `passed` direct evidence。
- `conditional` 必須明示是否被 responsibility 觸發。`triggered: true` 必須有
  `passed` evidence；`triggered: false` 只能是 `not-applicable`，並要交代理由，不能用
  空白或「未檢查」冒充 N/A。
- `not-current` 表示現時 Fractal System／Workplace 沒有使用該種 platform、hardware、
  media 或 service surface。若日後引入，必須先重新分類，不能沿用舊 N/A。

12 個 universal sources 是 `Accessibility`、`Design principles`、`Feedback`、
`Foundations`、`Generative AI`、`Inclusion`、`Loading`、`Offering help`、`Privacy`、
`Status`、`Undo and redo` 及 `Writing`。八項 principles 和十項 cross-cutting
requirements 合起來必須引用齊這 12 個 sources；validator 會拒絕任何遺漏。

## 八項 principles

每項 persistent responsibility 都要逐項提供 evidence：

1. `Purpose`：有清楚而有意義的人類結果，改善不偏離責任存在的問題。
2. `Agency`：人知情、有控制權、有選擇，亦可取消、復原和從錯誤返回。
3. `Responsibility`：以人的利益、安全、私隱、透明度和 data minimisation 為先。
4. `Familiarity`：沿用一致而熟悉的 Fractal 概念，給予清楚 feedback，不另造平行系統。
5. `Flexibility`：從開始照顧 accessibility、多種情境、輸入和 platform intention，並保留
   context。
6. `Simplicity`：清楚、直接、精簡而有 hierarchy；只留下完成工作所需內容，但不以
   minimalism 刪走必要 evidence 或 control。
7. `Craft`：細節、品質、可靠性、真實世界測試、反覆改良和 release 後維護同樣重要。
8. `Delight`：整個 experience 要帶來有目的的人類價值，而不是表面裝飾。

`Delight` 不能由 deterministic test 自我證明。Staged audit只可以記錄
`human_qualitative_acceptance: pending` 同 observable proxy evidence；release gate仍會要求
`human_qualitative_acceptance: true` 以及獨立 `human_qualitative_evidence_ids`。因此，
「所有 tests pass」不等於「人覺得 experience 有意義」。

## Cross-cutting requirements

八項 principles 以外，每項 responsibility 亦要通過以下十項共同要求：

- `Generative AI`：AI 行為要透明、可控制、有邊界、可復原，並評估有用程度與可預見傷害。
  AI 只可支援有 evidence 的 `Continuous Improvement`，不能取代 primary-user authority 或
  `System Review`。
- `Accessibility`：從開始確保結果可感知、可理解、可適應及可操作，而不是完成後才補救。
- `Inclusion`：文字、假設、default 和評估均照顧不同的人和情境，避免 stereotype 和排除。
- `Privacy`：只收集、保留、展示和傳送工作真正需要的資料，並提供清楚目的、保護、同意和控制。
- `Writing`：用人話、精簡、一致、可行動的 user-facing 語言，不把內部 architecture 當答案。
- `Feedback and status`：重要操作要適時交代狀態、結果、錯誤和下一個可做行動。
- `Undo and recovery`：按風險提供 cancel、undo、restore 或安全 recovery，trial 和改動亦有已測
  restoration path。
- `Loading and progress`：等待過程要 responsive、可量度時要有誠實 progress，未有 evidence
  前不可聲稱完成。
- `Offering help`：在需要的位置提供下一步，而不是用大量說明掩蓋本來可以修正的 primary path。
- `Continuous Improvement core`：Apple alignment 只強化既有 purpose、真實測試、iteration
  和 maintenance；不可建立第二條 review 或 lifecycle。

## Responsibility acceptance contract

`validate_responsibility_alignment(alignment, registry)` 是 fail-closed boundary。每一份
alignment 必須同時包含：

- 一個 `responsibility_id`；
- `continuous_improvement.is_core: true`、`status: passed`、direct evidence，以及唯一既有
  `project-review -> system-review` path；
- 依 canonical order 齊全的八項 `principle_alignment`；
- 依 canonical order 齊全的十項 `requirement_alignment`；
- 綁定171項 Source set嘅 `registry_manifest_sha256`，以及 `universal`、`conditional`、
  `not_current` 三組 `source_applicability`；validator會按 Registry展開並驗證 exact set equality，
  唔會喺每一項 responsibility重複保存171筆 Source metadata；
- `conditional.triggered_source_ids` 每項要有 direct evidence，其餘只可以用明確
  `all-except-triggered` N/A reason；
- `Delight` 的 direct human qualitative acceptance。

Validator 只回傳 validated detached value，不寫 Project、Workplace 或 active runtime；亦不作
activation 或 publication。真正的 acceptance 仍由既有 `System Review` 按 canonical Project
state、Reality Check evidence 和 primary-user authority 作出。

`validate_apple_version_acceptance()` 係最後 build gate：佢會將 exact 171-source manifest、
20組 persistent responsibilities、component audit、user-surface audit、Project revision、decision
batch同 primary-user qualitative `Delight` acceptance綁成一張 integrity receipt。張 receipt只係
candidate build input，唔係 activation／publication authority；active pointer、fresh session、trusted
Hook ledger、remote acknowledgement同restore仍要由 `$version` lifecycle逐項證明。
