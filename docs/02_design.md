# Template Design Specification — CMN-C2-270 Employee Financial Wellness Self-Advisory Agent

## Position in AgentCore Architecture

- **Agent Class**: `WellnessAdvisoryGraph`（外側）+ `WellnessWorkflowGraph`（内側 domain workflow）
- **L1 Base**: **AgentBaseGraph**（AutonomousBaseGraph ではない）
  - 外側グラフは L1 の `AgentBaseGraph` を直接継承した固定 5 ノード backbone。
  - Cat 2 標準構成: `main` スロットに `GraphNode` サブクラスを置き、内側の多段ドメイン
    ワークフロー（`src/graph/domain_workflow_graph.py`）を包む。
- **Three-Layer Separation**:
  - State: flat TypedDict composition（Pydantic 不使用 — msgpack 非互換）
  - Node: L1 inheritance（Template Method: `execute(self, state) -> dict` のみ override・部分 dict 返却）
  - Graph: composition（`register_nodes()` によるノード差し替え・`add_edges()` は override しない）
- **Entry adapters**: HTTP は `src/api/server.py`、AGENTIC STAR Marketplace コンテナ実行は repo 直下 `cli.py`（同一 graph の境界 adapter）

## Architecture Overview

### Node Configuration（外側 backbone）

| Node | Responsibility | Input State | Output State | Inherits/Overrides |
|------|---------------|-------------|--------------|-------------------|
| initialize | schema_version / session_id / trust_level 設定 | user_input | schema_version, session_id | InitializeNode (default) |
| pre_process | 従業員プロファイルの JSON 正規化・検証、検索クエリ生成 | user_input | validated_input | `EmployeeProfilePreprocessNode`（PreProcessNode 継承）|
| main | 内側 domain workflow を GraphNode で実行 | validated_input | plan_projection, peer_trends, advisory_output, status | `WellnessWorkflowGraphNode`（GraphNode 継承）|
| post_process | 出力整形・監査 emit・個別商品推奨ガードの最終確認（S-3） | advisory_output | output | `AdvisoryPostProcessNode`（PostProcessNode 継承）|
| finalize | response_metadata / total_time_ms | — | response_metadata | FinalizeNode (default) |

### Inner domain workflow（`main` スロットが包む内側グラフのノード）

内側 `WellnessWorkflowGraph`（**`BaseGraph` を直接継承**）が以下の多段ワークフローを実装する。
内側は 3 ノードの完全カスタムトポロジ（順次実行）なので `BaseGraph` で十分であり、`AgentBaseGraph`
は選ばない — 内側で `AgentState` / `InvocationContext` / S-ゲートを二重管理すると、外側 backbone の
S-ゲートと inner の S-ゲートが二重実行される（F-01・レビュー指摘）。`BaseGraph` の 7 抽象
メソッド（`register_nodes()` は `super()` 呼ばない・`route()` / `get_output()` 等）を実装する。

| Inner Node | Responsibility |
|------|---------------|
| `LifePlanProjectionNode` | 資産形成の将来値を**自前の決定論計算モジュール**で試算。年次繰り上げ計算（資産残高 = 前年残高 × (1+利回り) + 年間収入 − 年間支出、ライフイベント/退職金を加算）で 100 歳までの世帯収支を投影。公的年金収入は概算式（報酬比例部分＋基礎年金）で見積もるか、ねんきん定期便の見込み額を入力受領。利回り・インフレ率・昇給率等の前提は config 化 |
| `PeerTrendRetrievalNode` | 同社内の匿名化資産形成傾向 RAG（ベクトル検索）から、同年代・同職位のパターンを検索。k-匿名性で粗く集計された参考情報のみ |
| `WellnessAdvisorySynthesisNode` | 試算値＋同僚傾向を LLM で統合し、本人向けの「考える材料」を生成。**個別金融商品推奨ガード付き**（プロンプト制約 + 出力検証）|

> 論理 4 ノード（EmployeeProfilePreprocess / LifePlanProjection / PeerTrendRetrieval /
> WellnessAdvisorySynthesis）のうち、プロファイル正規化は外側 `pre_process` に、残る 3 段は内側
> domain workflow に配置する。これにより Cat 2 の「複雑さを inner graph に閉じ込める」原則に沿う。
>
> **計算の所在（2026-07-06 確定・当初案からの変更）**: 当初は金融庁ライフプラン／厚労省年金の
> 公的シミュレーターの**ロジックに委譲**する想定だったが、一次情報確認の結果、**金融庁ライフプラン／
> つみたてシミュレーターには民間が組み込める API・配布プログラム・利用申請スキームは存在しない**
> （Web UI のみ）。厚労省公的年金シミュレーターはプログラムが民間公開されているが**年金額の試算のみ**
> で、本 AG が必要とする資産形成ライフプラン試算はカバーせず、かつ利用に行政申請（企画書→事前打合せ
> →利用申請）を要する。したがって **ライフプラン／年金概算の計算はいずれも公知アルゴリズムに基づく
> 自前の決定論モジュールで実装**する。厚労省プログラムの正規申請連携は、精度向上を求める企業向けの
> **将来のオプション連携**として位置づけ、本 AG のリリースをブロックしない。

### Data Flow

```
START → initialize → pre_process → main → {route} → post_process → finalize → END
                                            ↓ (retry, max 3)
                                          pre_process

  main (WellnessWorkflowGraphNode.execute) 内側:
    LifePlanProjectionNode → PeerTrendRetrievalNode → WellnessAdvisorySynthesisNode
    → get_output() → merge_output() で外側 State へ射影
```

### State Definition（flat TypedDict / AgentState 継承）

| Field | Type | Purpose | Required |
|-------|------|---------|----------|
| `user_input` | `str` | 生の従業員プロファイル入力（JSON 文字列）| yes |
| `validated_input` | `str` | 正規化・検証済みプロファイル + 検索クエリ | yes |
| `plan_projection` | `dict` | 自前決定論計算の試算結果（100 歳までの資産残高推移・年金概算等の数値）| yes |
| `peer_trends` | `list` | 同年代・同職位の匿名化傾向（k-匿名性集計・個人特定不可）| yes |
| `advisory_output` | `str` | 本人向けの考える材料（個別商品推奨を含まない）| yes |
| `output` | `str` | post_process 整形後の最終出力 | yes |
| `status` | `str` | AgentStatus 値（`success` / `error` / `retry` 等）| yes |

**State Constraints (mandatory):**
- Flat TypedDict only（primitives + JSON-serializable types）
- No JWT, API keys, credentials in State（checkpoint DB 漏洩）— 従業員プロファイルの
  センシティブ生値も参照 ID 化を検討し、checkpoint に生値を残さない方針とする
- InvocationContext は `config["configurable"]` 経由のみ（State には入れない）
- No Pydantic models, dataclass, arbitrary Python objects（msgpack 非互換）

## Framework Utilization

### Shared Components Used
- [x] InvocationContext（correlation_id, session_id, permissions, credential handle）
- [x] ConnectionPolicy（retry/timeout strategy）— ベクトル DB（社内傾向 RAG）呼び出しの retry
- [x] SecurityViolationError
- [x] S-2: `_extra_security_gate_input()` — 従業員プロファイル入力の PII / 同意フラグ検証
      （既定 PII スキャンの後に、匿名化前提の取り扱い方針・入力サイズ上限を追加チェック）
- [x] S-3: `_extra_security_gate_output()` — **個別金融商品推奨の構造的禁止**（下記「S-3 商品推奨ガード
      （厳密・多層防御）」参照）と、社内傾向が **k-匿名性（既定 k=10）** を満たすことの検証
      （個人特定可能な粒度の出力を拒否）。詳細は下記専用節を正本とする
- [x] S-4: `emit_trace_event()` — 各 `execute()` 内でドメインイベントを最低 1 回
      （`node_start`/`node_complete`/`node_error` は `BaseNode.__call__()` が自動 emit するため重複禁止）

> **S-2/S-3 gate behaviour by node type (ADR-017):**
> - `FunctionNode` subclass（内側 3 ノード・pre/post）→ framework `@final` ゲートが自動実行；
>   拡張は `_extra_security_gate_input()` / `_extra_security_gate_output()` のみ
> - `GraphNode`（外側 `main` = `WellnessWorkflowGraphNode`）→ 意図的 no-op passthrough
>   （内側ノードのゲートが既に適用されるため）
> - S-3 の個別商品推奨禁止ガードは **多層防御（L1 プロンプト抑止 → L2 生成後検査 → L3 S-3 最終ゲート
>   → L4 免責明示）** で実装する。詳細は下記「S-3 商品推奨ガード（厳密・多層防御）」を正本とする

### Composition Pattern

- **Pattern**: GraphNode (subgraph)
- **Composition target**: `WellnessWorkflowGraph`（`src/graph/domain_workflow_graph.py`）
- **Error propagation strategy**: **propagate**（内側の一般例外は `SubgraphError` として再送出し fail fast。
  ただし peer-trend 検索時の接続障害だけは DDR の 029 型縮退境界で `([], True)` に変換し、projection-only
  advice を継続する。pre→main は無条件辺のため、それ以外の main / post エラーは上流へ伝播する）

## Import Isolation Confirmation
- [x] Template does not import agenticstar-platform SDK (Level 0)
- [x] Import targets: framework/ and shared/ only（agents/base/ は不要）

## Design Decision Record

| Decision | Option A | Option B | Chosen | Rationale |
|----------|----------|----------|--------|-----------|
| L1 base type | AgentBaseGraph | AutonomousBaseGraph | **AgentBaseGraph** | 固定多段パイプラインで完結し自律ループ不要（Cat 2）|
| Composition pattern | Standalone (単一グラフ) | GraphNode subgraph | **GraphNode subgraph** | ライフプラン試算→傾向検索→統合の多段ドメイン複雑性を inner graph に閉じ込める Cat 2 標準構成 |
| 計算の所在 | 公的シミュロジック委譲 | 自前決定論計算モジュール | **自前決定論計算** | 金融庁ライフプランに民間組み込み API/配布は存在せず（一次確認済）、厚労省は年金のみ＋行政申請要。ライフプラン試算は公知アルゴリズムで自前実装可・リリースを申請待ちでブロックしない |
| 個別商品推奨 | 許容（robo-advisor）| 構造的禁止 | **構造的禁止（多層防御）** | 適合性原則・robo-advisor 規制の回避。法令リスクのため下記「S-3 商品推奨ガード」の多層防御で厳密に担保 |
| k-匿名性しきい値 | k=5 | k=10（config 可変・下限）| **k=10（既定）** | 社内・同年代同職位という狭母集団の機微データ。実務中心帯（k=5〜11）の上限を安全側の既定に。k 未満は「該当なし」フォールバックで個人特定を構造的に不可能化 |
| peer-trend store | SDK 1.0.2 の STUB `PgVectorStore` | SDK 1.0.2 の実装済み `QdrantVectorStore` | **`QdrantVectorStore` を直接使用** | PgVectorStore は search/upsert/delete が未実装で KB が恒久的に空になる。Qdrant の `payload` は repo 内アダプタで `metadata` へ写し、既存 k-匿名ゲートを維持する |
| Qdrant の分離方式 | `qdrant-scoped-backend` 部品 | SDK `QdrantVectorStore` の namespace/agent tag + deployment 境界 | **SDK を直接使用** | 本 AG の顧客内 KB に区画キーがなく、同部品の「使わない条件」に該当する。会社分離は企業ごとの専用 Qdrant endpoint/deployment で担保し、架空の request scope を導入しない |
| peer-trend 起動設定エラー | 全例外を握りつぶして縮退 | 未設定/明示無効のみ縮退、不正設定は起動失敗 | **未設定は縮退／有効だが不正は fail fast** | 029 型は機能未採用または検索時の一時障害に限定する。有効化した設定の欠落・型不正・初期接続失敗は deployment defect であり、「store 未設定」に偽装しない |

## S-3 商品推奨ガード（厳密・多層防御）

> ⚠️ **法令リスク（金商法・適合性原則・robo-advisor 規制）に直結するため、単一の検査に依存しない
> 多層防御（defense-in-depth）で設計する。** 1 層でも「個別金融商品の推奨・勧誘」が漏れたら
> それは規制違反になり得る。以下すべてを実装する。

| 層 | 場所 | 内容 |
|---|---|---|
| **L1 生成抑止（プロンプト制約）** | 内側 `WellnessAdvisorySynthesisNode` | LLM システムプロンプトで「特定の金融商品（銘柄・投資信託名・保険商品名・証券会社名等）を挙げない・推奨しない・勧誘しない。一般的な資産形成の考え方と社内傾向の提示に限る」を強制。few-shot で禁止例／許可例を提示 |
| **L2 出力検査（生成直後）** | 内側 `WellnessAdvisorySynthesisNode`（生成後・返却前）| 生成テキストを禁止パターン検査：①個別商品名・ティッカー・ISIN 等の固有名詞辞書照合 ②推奨・勧誘動詞（「買うべき」「おすすめ」「〜に投資しなさい」等）＋商品文脈の共起検出。ヒットしたら **再生成（既定 max 2 回＝計 3 試行・config `guard_regen_max` で可変）→ なお違反なら error status** |
| **L3 S-3 ゲート（最終・非バイパス）** | 外側 `AdvisoryPostProcessNode._extra_security_gate_output()` | フレームワーク S-3 の `@final` 後に走る最終ゲート。L2 と同等の検査を**独立実装**で再実行（L2 のすり抜け・実装バグに対する冗長化）。違反検出時は出力を返さず `SecurityViolationError` を送出。**ここは「フィルタして通す」ではなく「違反なら拒否（fail-closed）」** |
| **L4 免責・非勧誘の明示** | 外側 `AdvisoryPostProcessNode` | 出力に必ず「本情報は一般的な資産形成の参考であり、特定の金融商品を推奨・勧誘するものではありません」の免責文を付与（preservation variant：欠落したら S-3 で拒否）|

- **禁止語・商品名辞書**は config 化し、運用で更新可能にする（新商品・新証券会社に追随）。
- **fail-closed 原則**：判定に迷う／検査器が例外を出した場合は「安全側＝出力しない」に倒す。
- **監査**：各層の判定結果を `emit_trace_event`（S-4）で記録し、後から「なぜ通した／拒否した」を追跡可能に。

## 決定論計算パラメータ既定値（お任せ確定・config で上書き可）

`config/config.yaml` に既定値を持ち、企業ごとに config で調整可能とする。一般的なライフプランニングの
保守的な既定を採用（数値は目安・運用で調整）。

| パラメータ | 既定値 | 備考 |
|---|---|---|
| `annual_return_rate`（想定運用利回り）| 3.0% | 長期分散投資の保守的想定。リスク資産比率で調整余地 |
| `inflation_rate`（インフレ率）| 2.0% | 日銀物価目標水準 |
| `salary_growth_rate`（昇給率）| 1.5% | 名目昇給の保守的想定 |
| `retirement_age`（想定退職年齢）| 65 | 公的年金受給開始と整合。config 可変 |
| `projection_end_age`（投影終了年齢）| 100 | 金融庁ライフプランと同じ長寿前提 |
| `pension_estimation_mode` | `certificate_input` | 既定は「ねんきん定期便の見込み額を入力受領」。`formula_estimate`（概算式）に切替可 |
| `k_anonymity_min`（k-匿名性下限）| 10 | S-3 で強制。未満セグメントは傾向を返さない |
| `guard_regen_max`（S-3 L2 再生成上限）| 2 | 商品推奨違反検出時の再生成回数上限（計 3 試行）。超過で error status |

> これらは**決定論**（同一入力→同一出力）であること。乱数・時刻依存を計算に入れない（再現性・監査性）。
> ただし `guard_regen_max` は LLM 生成の非決定性に対する安全弁で、計算パラメータではない。

## Open Design-Phase Items

- ✅ **ライフプラン計算の前提パラメータ** → 上記「決定論計算パラメータ既定値」で確定（config 上書き可）。
- ✅ **k-匿名性しきい値** → **k=10 を既定**（config `k_anonymity_min` で可変・下限）。未満セグメントは
  傾向を返さないフォールバックで個人特定を構造的に不可能化。実務中心帯（k=5〜11）の上限を安全側に採用。
- ✅ **個別商品推奨ガード** → 上記「S-3 商品推奨ガード（厳密・多層防御）」で L1〜L4 の 4 層 fail-closed に確定。
- ✅ **L2 再生成回数** → 既定 max 2 回（計 3 試行）に確定・config `guard_regen_max` で可変（F-02）。
- **（実装フェーズ詳細）** 禁止語・商品名辞書の初期エントリ整備、集計粒度（年代の刻み・職位区分の定義）
  の詳細設計。
- **（将来オプション）厚労省公的年金シミュレーター プログラムの正規申請連携** — 精度向上を求める
  企業向けの後日オプション。企画書→事前打合せ→利用申請の行政プロセスを要し、本 AG のリリース前提には
  含めない（申請主体が導入企業か提供側かは別途整理）。

## 変更履歴

- **2026-08-31**: peer-trend store を SDK 1.0.2 の STUB `PgVectorStore` から実装済み
  `QdrantVectorStore` へ変更。SDK hit の `payload` を既存 k-匿名ゲート用 `metadata` に写す repo 内
  adapter、FastAPI lifespan 所有の `AsyncServiceRuntime`、未設定/無効のみ縮退・有効な不正設定は
  startup fail-fast とする境界を追加。区画キーがないため `qdrant-scoped-backend` 部品は不採用。
- **2026-07-06**: 一次情報確認により「公的シミュロジック委譲」→「自前決定論計算モジュール」へ設計変更。
  金融庁ライフプラン／つみたてシミュレーターには民間組み込み API・配布プログラム・利用申請スキームが
  存在せず（Web UI のみ・利用規約は個人利用前提）、厚労省公的年金シミュレーターは年金額試算のみ＋
  行政申請要のため。moat は「社内匿名化傾向 RAG」に一本化（元より本 AG の差別化はこれ）。
- **2026-07-06 (レビュー対応)**: 内側 `WellnessWorkflowGraph` の継承元を **`BaseGraph`
  に確定**（二重 S-ゲート回避・F-01 Medium）。S-3 L2 再生成回数を **既定 max 2 回（config `guard_regen_max`）**
  に確定（F-02 Low）。F-03（proposal の旧「委譲で法令改正に強い」記述）は既に自前計算へ修正済みで残存なしを確認。
- **2026-07-06**: k-匿名性しきい値を **k=10 既定**（config 可変・未満は非表示）に確定（実務中心帯 k=5〜11 の
  上限を安全側に採用）。S-3 商品推奨ガードを **4 層 fail-closed（プロンプト抑止／生成後検査／S-3 最終ゲート
  ／免責明示）** の厳密設計に確定（法令リスク対応）。決定論計算の既定パラメータを確定（利回り 3%/インフレ
  2%/昇給 1.5%/退職 65/投影 100 歳・config 上書き可）。
