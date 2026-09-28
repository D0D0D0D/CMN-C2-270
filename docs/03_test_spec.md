# Test Specification — CMN-C2-270 Employee Financial Wellness Self-Advisory Agent


## AGENTIC STAR Marketplace 実機疎通（2026-08-31・PASS）

AGENTIC STAR DEV 環境の User Page から実際に invoke し、runner Pod の実行結果と応答を確認した。

| 項目 | 実測 |
|---|---|
| 入力 | 「34歳、手取り月32万円、財形貯蓄を毎月14万円やっています。同世代の人はどうしていますか？」 |
| runner Pod | 実行 13.0 秒 / exit 0 / agent コンテナのログ 0 行（正常完了時は出力しない） |
| 応答 | 日本語の資産形成アドバイス（現状整理 → 必要資金の期間分割 → 継続可能な範囲での検討 → 複数想定での見直し） |

末尾に「本情報は一般的な資産形成の参考であり、特定の金融商品を推奨・勧誘するものではありません」の非勧誘免責文が付与されることを確認。KB 未投入のため peer trend の実値は提示されない。

KB は未投入のため、確認できたのは「該当情報が無いときに正しくそう返すこと」までである。
KB 投入後の検索精度はこの記録の対象外。


## 実 LLM 1 回転チェック（2026-08-31・PASS）

新アダプタ経由で peer-trend が**実際にプロンプトへ入り出力に効くところまで**確認した。
`status=success` だけでは projection-only 縮退と区別が付かないため、内容到達を名指しで検査している。

| 層 | 実物か | 根拠 |
|---|---|---|
| LLM | ✅ 実物 | ローカル CLI を刺した `BaseLLM`。`rc=0 content=619字 prompt=390字` |
| peer-trend store | ❌ SDK 形状の fake | 実 Qdrant を起動できないため。**SDK `QdrantVectorStore` と同じ hit 形状** `{id, payload, score, similarity}` を返す fake を `PeerTrendQdrantAdapter` に通した |

検査した性質:

- **k-匿名ゲート**: `group_size` 20000 / 15000 / **3** の 3 件を投入し、**2 件のみ通過**（k 未満の 1 件を除外）。
- **観測signal**: `store_unavailable=False`（store が答えた）。STUB 時代の「常に True」から回復している。
- **内容到達**: peer-trend 由来の語（血圧 / 有酸素 / 睡眠 / スクリーン）が **4/4 出力に現れた**。
- **漏洩なし**: k 未満で落とした行の文言が出力に**現れないこと**を確認。

既知の限界: store が fake のため、実 Qdrant 接合（接続・collection 作成・埋め込み生成）は未検証。

## Test Strategy
- Test types: Unit / Integration / Proof-of-Boundary
- Local runner: `python -m pytest tests/ -q`
- 重点: ①S-3 個別商品推奨ガードの fail-closed ②k-匿名性ゲート ③決定論計算の再現性
  ④entry-adapter v0.4.0 operation policy ⑤inner→outer `error_code` 4-hop

## Framework Compliance Tests (Mandatory)

| TC-ID | Test | Expected Result | Result |
|-------|------|----------------|--------|
| TC-01 | State contract: flat TypedDict | `src/schemas/state.py` は AgentState 継承・primitives のみ（PB-2 でも検証）| ✅ PASS |
| TC-02 | SecurityViolationError fires on invalid output | S-3 L3 が個別商品推奨を検出し `SecurityViolationError` 送出（`test_nodes.py::test_l3_gate_rejects_product_recommendation`）| ✅ PASS |
| TC-03 | No JWT/Credential in State/src | CI `gate-credential-scan`: 0 violations | ✅ PASS |
| TC-04 | InvocationContext via configurable only | State には格納しない（server.py で ctx 生成・inner へは ctx 経由）| ✅ PASS |
| TC-05 | S-4: no duplicate lifecycle events in `execute()` | `node_start`/`node_complete`/`node_error` は execute 内で emit しない（domain event のみ）| ✅ 0 duplicates |
| TC-06 | S-2: `_security_gate_input()` not overridden (FunctionNode) | 直接 override せず `_extra_security_gate_input()` 利用（pre_process）| ✅ 0 overrides |
| TC-07 | S-3: `_security_gate_output()` not overridden (FunctionNode) | 直接 override せず `_extra_security_gate_output()` 利用（post_process）| ✅ 0 overrides |
| TC-08 | `required_trust_level` enforced | 全ノード `VERIFIED_EXTERNAL` を明示（`test_nodes.py::test_trust_level_explicit_and_valid`）| ✅ PASS。加えて **不足 trust では `execute()` に到達しないこと**を証明 — PB-6 `TestInvokeOrder::test_s1_denial_refuses_execution_before_execute` が `_PrivilegedTrustGateFixture` を不足 trust で `node(state)` 呼びし、`status=error` / `error_log[0]` の `S-1 trust gate denied` / `events == ["s1_denied"]` / **`execute()` 未呼び出し** を assert（正本 docs/03 の TC-08 要求） |
| TC-09 | S-2: `_extra_security_gate_input()` non-trivial | pre_process が入力サイズ上限を検査 | ✅ PASS |
| TC-10 | S-3: `_extra_security_gate_output()` non-trivial | post_process が個別商品推奨を再検査し fail-closed | ✅ PASS |
| TC-11 | S-4: ≥1 domain `emit_trace_event()` per `execute()` | 全ノードで domain event を emit | ✅ ≥1 per node |

## Proof-of-Boundary Tests (Mandatory)

| PB-ID | Boundary | Test | Expected Result | Result |
|-------|----------|------|----------------|--------|
| PB-1 | BaseNode → AuditLogger | `emit_trace_event()` fires（各ノード execute 内）| No silent failures | ✅ PASS |
| PB-2 | State serialization | `state.py` は primitives のみ（AST scan）| No Pydantic/dataclass/credential | ✅ PASS |
| PB-3 | L1 → External service | ベクトルストア/LLM は注入シームで抽象化（実接続は STG）| 注入シーム検証（unit で fake 注入）| ✅ PASS (fake) |
| PB-4 | Import isolation | No Level 0 imports（AST scan・`src/`）| 0 violations | ✅ PASS |
| PB-5 | Checkpoint safety | State にセンシティブ生値・Pydantic なし | Inspection pass | ✅ PASS |
| PB-6 | Invoke execution order | `__call__()`: S-1 → S-4 node_start → S-2 → execute → S-3 → S-4 node_complete（全ノード）| Order verified | ✅ PASS |
| PB-7 | HITL interrupt propagation | 非 HITL（`config/agent.yaml` / `config/config.yaml` とも `hitl.enabled` なし）| Auto-waived | ⏭️ N/A |

## Parts / Retrofit Regression Tests

| ID | Test | Expected Result | Result |
|----|------|----------------|--------|
| N-6/N-7 | entry-adapter v0.4.0 operation-aware auth | standalone token は READ のみ、write/resume は middleware identity 必須。CMN-C2-270 は `SCOPE_KEYS = ()` の scope-less 宣言 | ✅ PASS |
| M-1 | Manifest schema migration | `config/agent.yaml` は root-level manifest、`class` は dotted path、`requires.extras` は list | ✅ PASS |
| M-2 | Runtime config hoist | 旧 `agent.config` の実効値が `config/config.yaml` から server graph config に入る | ✅ PASS |
| G-4 | GraphNode subgraph failure projection | `graph.invoke()` 戻り値に `error_code` が残り、provider 例外文字列は外部応答に出ない | ✅ PASS |
| X-2 | Mutation check | outer `get_output()` の `error_code` 射影を外すと G-4 が赤になる | ✅ FAIL confirmed |
| X-3 | Qdrant hit adapter mutation | `payload` から `metadata` への写しを `{}` にすると BL-13 が赤になる | ✅ FAIL confirmed / restored |
| X-4 | k-匿名 gate mutation | `group_size >= k` 条件を外すと BL-14 が赤になる | ✅ FAIL confirmed / restored |
| X-5 | startup validation mutation | 必須 key 欠落 check を無効化すると BL-16 が赤になる | ✅ FAIL confirmed / restored |

## Business Logic Tests

| TC-ID | Test | Input | Expected Result | Result |
|-------|------|-------|----------------|--------|
| BL-01 | 決定論計算の再現性 | 同一プロファイル 2 回 | 同一 projection（乱数/時刻依存なし）| ✅ PASS |
| BL-02 | 100 歳まで投影 | current_age=40 | trajectory 61 年分・終端 age=100 | ✅ PASS |
| BL-03 | 資産枯渇検出 | 支出過多プロファイル | `depletion_age` 検出 | ✅ PASS |
| BL-04 | k-匿名性: k 未満は除外 | group_size=3 の傾向（k=10）| 除外され返さない（fail-closed）| ✅ PASS |
| BL-05 | k-匿名性: group_size 欠如は除外 | group_size なしの hit | 除外（fail-closed）| ✅ PASS |
| BL-06 | 匿名化サニタイズ | raw employee_id を含む hit | 出力に raw 識別子を含めない | ✅ PASS |
| BL-07 | S-3 L2 ガード: 商品推奨を阻止 | 商品名を返す LLM | error status・出力漏洩なし（再生成後も違反で error）| ✅ PASS |
| BL-08 | S-3 L4 免責付与 | clean advisory | 出力末尾に非勧誘免責文 | ✅ PASS |
| BL-09 | S-3 ガードが自己免責文で誤検知しない | 免責文のみ | violation 0（回帰）| ✅ PASS |
| BL-10 | フル invoke happy path | 有効プロファイル + fake store/LLM | SUCCESS・免責付き・k ゲート済 peer_trends | ✅ PASS |
| BL-11 | フル invoke: 商品推奨 LLM は fail-closed | BadLLM | ERROR・出力なし・`error_code=product_recommendation_guard_failed` | ✅ PASS |
| BL-12 | 空入力 | 空文字 | ERROR（クリーン）| ✅ PASS |
| BL-13 | SDK Qdrant hit shape adapter | `payload.group_size=25` の `{"id","payload","score","similarity"}` | payload を metadata に写し、k-gate 通過後の匿名化 trend を返す | ✅ PASS |
| BL-14 | Qdrant payload の k-匿名 fail-closed | `group_size` 欠落 / 9（k=10） | いずれも除外、store は応答済みなので unavailable=false | ✅ PASS |
| BL-15 | peer-trend store 未設定 | store config section なし | `([], True)` 相当の projection-only 縮退を維持 | ✅ PASS |
| BL-16 | peer-trend store 設定不正 | enabled=true かつ必須 config 欠落 | startup wiring が `ValueError` を送出し、未設定扱いにしない | ✅ PASS |
| BL-17 | SDK/runtime lifecycle | 有効な config + fake Qdrant/runtime | embedding secret を注入し adapter を配線、shutdown で runtime close | ✅ PASS |

## Test Execution Summary
- Local: 指定の SDK 1.0.2 runner で `python -m pytest tests/ -q` → **153 passed, 2 skipped**
- Ruff: `ruff check src tests` / `ruff format --check src/` → All checks passed / 22 files already formatted
- `parts/tools/verify_part.py .` → **SUSPECT 0**（`entry-adapter` v0.4.0、`SCOPE_KEYS = ()` と formatter guard の ADAPT 2 件）
- `work/scripts/local_ci.py . --only <blocking gates>,lint-format,run-tests,ruff` → **全通過**（17 jobs）
- `mypy src/` / CI `type-check` は allow_failure の既存 mypy 負債により FAIL（framework/shared の `py.typed` 不在等）
- PB (3) + Unit (services + nodes) + Integration (full graph) を含む
- 実 LLM は検証環境では実行不可。静的確認: LLM 呼び出しは `complete(messages)` のみ、プロンプト 299 字、試算値・peer trend 実値・非勧誘制約を含む。deterministic fallback は `advisory_output` を常に埋め、LLM 固有の別フィールドは無い
