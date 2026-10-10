# 本地 DP 分支（0.5.0-dp）—— 身份与运行记录

> 生成时间：2026-10-10 · 生成模型：deepseek-v4.1-flash（DeepSeek Harness）· 状态：有效（单种子、单次观测，不是胜率证据）· 关联文档：[../../docs/DP.md](../../docs/DP.md)、[../i100/results.md](../i100/results.md)

## 这个目录是什么

本目录记录**本地 DP 分支**（版本 0.5.0-dp）的身份与真机运行。它不是新的发布证据，也不替代上游 [release/i100](../i100/)：上游 i100 的默认档、评估与证据在本分支中**未改动**（`release/i100/defaults.json` 仍是 `version: 0.4.0b1`）。

- 解析后的固定设置：[defaults.json](defaults.json)
- 固定档配置：[`tools/solver_configs/i100-dp-strict-research.json`](../../tools/solver_configs/i100-dp-strict-research.json) = `minutes: 45`，`set: [feature-profile=i100, route-mode=force-then-search, low-io, event-driven-settle]`

## 运行记录（种子 1899537279，45 分钟档）

命令：

```powershell
python tools/run_solver.py --seed 1899537279 --minutes 45 --name i100-dp-strict-1899537279-45m-v4 ^
  --config tools/solver_configs/i100-dp-strict-research.json
```

| 运行 | 状态 | 墙钟 s | evaluations | 说明 |
| --- | --- | --- | --- | --- |
| v1 | VERIFIED_WIN_IN_NATIVE_HOST | 663.71 | 278 | 优化前（串行 strict） |
| v2 | VERIFIED_WIN_IN_NATIVE_HOST | 209.59 | 124 | 优化前 + 地图释放 |
| v3 | VERIFIED_WIN_IN_NATIVE_HOST | 100.20 | 70 | 优化后，显式 `--set route-workers=6` |
| v4 | VERIFIED_WIN_IN_NATIVE_HOST | 124.26 | 73 | 优化后，`--route-workers` 走自动检测路径 |

四臂 `lower_bound = upper_bound = 1`、`optimal_in_backend = true`、`game_equivalence_verified = false`、`normal_godot_verified = false`，`proof_scope = 'installed game commands in offline TestMode'`。

v4 细节：`best_label = eval-0017-macro_exploration`、`cache_hits = 0`、`trace_length = 588`、`trace_sha256 = 35235e722b3d3715867a42e688098fc2a23fcb1efabf58c841a008d5a6e5cb54`、`verification-timing.verified_wall_seconds = 120.00978230000328`。

二进制身份（四臂相同）：`host_sha256 = f52cca41a3e8d408950f1f863d8cedab80563754f3b8832fc832f8bd29128998`、`game_sha256 = 0861bfa1df347538d932f22d580e75420f08082792eb914e53b4882764acdbe9`、`godot_sha256 = 0e4897ecdfb31456a97c7d8028dfb8d7dbdc632e2f73fc9b438d7b266a139289`、`harmony_sha256 = ef1898322c9f5c86dc1b0758b272a9c440823b4a41ca9a0b82a3aa6b3d206387`。

## DP 链路证据（v4）

- `route-plan.json`：`spire-route-plan/v2`、`routes_scored = 18`、`top_n = 5`、top ids `[1,7,2,12,0]`、`alternatives = [8,9,3]`；`best.actions` 48 步、`best.score.total = 203.18`。
- `strict_routes` 5 条：route_id `1/7/2/12/0`，`plan_score` `203.18/203.18/199.24/199.24/185.87`，**全部 `NATIVE_ROUTE_DEATH`、`act = 0`**。
- `strict_continued = {'route_id': 1, 'tiers_dropped': true, 'map_released': true, 'map_preference': 'released: main search walks the whole map graph'}`；`search_metrics.route_filtered_branches = 47`、`route_released = true`。
- `seed-facts.json` 的 `wall_seconds = 8.63637369999924`。

## 能与不能得出的结论

**能得出**：DP 链路在真机上真实运行——处方地图被宿主执行、strict 五条各自评估一次、交还后主搜索自由走图并解出（`stop_reason = verified_boolean_maximum`）。`score_path` 的并行化在报告逐字节相同的前提下把 DP 规划段从 12.1 s 压到 2.7 s（见 [../../docs/DP.md](../../docs/DP.md) 第四节）。

**不能得出**：

- **不能宣称胜率提高**。硬约束：§18.13「DP 计划分不能预测路线存活」（Pearson −0.221）；本记录全部是**单种子单次观测**，种子 1899537279 是上游回归种子（TOASTY_MITTENS），不代表分布。
- **不能把 v1→v2 的 3.2× 当作优化收益**：那是 `--route-mode` 语义变化（地图释放）的结果；v2→v3 才是本轮优化的净效应，且 n=1。
- **不能把 `plan_score = 203.18` 当作路线质量的证据**：五条处方路线全部在第一幕第 14–17 层死亡。
