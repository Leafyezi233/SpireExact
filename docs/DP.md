# DP 路线插件（本地新增）

> 生成时间：2026-10-10 · 生成模型：deepseek-v4.1-flash（DeepSeek Harness）· 状态：有效 · 关联文档：[I100.md](I100.md)、[RELEASE_SCOPE.md](RELEASE_SCOPE.md)、[../README.md](../README.md)、[../release/local-dp/results.md](../release/local-dp/results.md)

## 现状速览

1. 本分支（版本 **0.5.0-dp**）在上游 i100 之上加了一个**整局 DP 路线规划器**。它默认**关闭**：不传 `--route-mode` 时，规划器不运行、宿主请求里没有 route 通道、结果文件里没有 `route_plan` 键，与上游 i100 逐项一致。
2. 规划器由三部分组成：`spire_exact/planning/walker.py`（走图，从原生侦察事实里长出候选路线）→ `spire_exact/planning/route.py`（对枚举出的地图路径做动态规划打分，产出地图动作序列 + 卡牌层级）→ `spire_exact/planning/search.py` 里的四阶段接线。
3. 四种模式（`off` / `guide` / `force` / `force-then-search`）定义在 `spire_exact/planning/dp_defaults.py:44`，解析器 `resolve_route_mode` 在 `dp_defaults.py:121`。**显式命令行参数永远赢过模式表**。
4. DP 与上游 i100 的关口诊所**互斥**（诊所只认 focus 调度器，DP 路线过滤只认 weighted/dfs）。启用 DP 时解析器会自动协调，并把改动了哪些键记进 `args.route_mode_changes`。
5. `--route-workers` 默认**自动检测本机物理性能核**（`dp_defaults.py:203` `detect_route_workers`，Windows CPU Sets，只数最快能效类里的物理核，不含超线程兄弟与小核；非 Windows 回退 1）。`score_path` 是纯函数，并行只改变墙钟，报告逐字节相同。
6. **本插件的计划分不预测路线存活**（历史结论 Pearson −0.221）。任何「计划分高 = 胜率高」的说法在真机对照前都不成立。

## 一、插件定位与调用

DP 是**旁挂**在规划器上的，不替换搜索。调用点只有两个：

- 命令行 / 启动器：`python -m spire_exact.planning --route-mode force-then-search …`；`--route-mode` 注册于 `spire_exact/planning/__main__.py:236`，取值校验与模式解析在 `__main__.py:305-321`。
- SpireBoard 面板：作业表单里的「DP 严格路线（strict then research）」勾选框（`dashboard/web/index.html` 的 `#solve-dp`，默认勾选）。服务端 `dashboard/jobs.py:92` `DP_ROUTE_MODE='force-then-search'`，`jobs.py:94-106` `dp_settings()` 从 `dp_defaults.route_mode_argv` 派生参数，`jobs.py:108-111` 按开关选 `solver_settings`。

固定档配置文件：`tools/solver_configs/i100-dp-strict-research.json`（`minutes: 45`，`set: [feature-profile=i100, route-mode=force-then-search, low-io, event-driven-settle]`）。

## 二、四种模式

| 模式 | 语义 | 搜索形态 |
| --- | --- | --- |
| `off`（默认，`PUBLIC_ROUTE_MODE`） | 完全不启用，上游 i100 原样 | focus 调度器 + 关口诊所 |
| `guide` | 出计划，作为**分配信号**交给普通搜索 | weighted 调度器，处方地图偏好生效，`walker_walks=30`、`route_top_n=1`、`route_prescribed=True` |
| `force` | 每条处方路线恰好评估一次，菜单被严格卡层级强制 | weighted 调度器，`walker_walks=30`、`route_top_n=5`、`route_strict=True`，主搜索让位 |
| `force-then-search` | 先严格跑 top-N 处方路线，再把**地图与卡牌层级全部交还**主搜索 | weighted 调度器，之后自由走图 |

模式表在 `spire_exact/planning/dp_defaults.py:51-87`（`ROUTE_MODE_TABLE`），字段名与 `SearchConfig` 逐项对齐（`dp_defaults.py:92` `MODE_FIELDS`）。非 off 时解析器会协调搜索形态：关 `clinic`/`clinic_hints`、`strategy_prior→off`、`scheduler→weighted`、关 i085 的 `macro_plateau`/`macro_widening`/`macro_fair` 与 `macro_routes`、`tail_mode→off`、`focus_stall→0`；`force` 与 `force-then-search` 另清空 `root_policies`（`dp_defaults.py:157-188`）。

## 三、strict-then-search 的四阶段

以 `route_mode=force-then-search` 为例，`spire_exact/planning/search.py` 的 `solve()` 依次跑：

| 阶段 | 位置 | 做什么 |
| --- | --- | --- |
| A 走图 | `search.py:950-955` | `walker.run` 走地图图，写 `seed-facts.json` |
| B 规划 | `search.py:956-966` | `route_planner.plan(facts, top_n=route_top_n, workers=route_workers)` 写 `route-plan.json`，装 `ev.route_actions`/`ev.route_tiers` |
| C 严格处方 | `search.py:1389-1451` | 对 `route_plan['top']` 每条**恰好一次**评估（先全部 dispatch、再统一 collect），`ev.prior_provider=None`；随后 `strict_then_search` 交还块把 `ev.route_tiers`/`ev.route_actions`/`ev.route_hints` 全部置空并 `scheduler.release_route_filter()`，并清扫 pending 里残留的 `route`/`policy_prior` |
| D 主搜索 | `search.py:1454` 起 | 从 strict 死亡处继续、失败导向的自由搜索，地图与卡牌层级都不再受 DP 约束 |

阶段 C 的交还语义（玩家指令 2026-10-10）：**丢弃的只是卡牌强制层级与地图偏好，包括宿主 `+500` 路线分和调度器 top-N 步骤过滤**。结果文件里以 `result['strict_continued']={'route_id':…,'tiers_dropped':True,'map_released':True,'map_preference':'released: main search walks the whole map graph'}` 记录。

阶段 A/B/C/D **之间是串行的**（设计如此）；下面说的并行都发生在单个阶段**内部**。

## 四、四项并行优化与本机实测

| # | 项 | 位置 | 是否改结果 |
| --- | --- | --- | --- |
| #1 | top-N 处方去重（重复计划不占名额） | `route.py:1546` `_plan_fingerprint`、`route.py:1559` `_distinct_plans` | 否（只去重，不改排序） |
| #2 | 严格阶段一次性并发（全部 dispatch 后再 collect） | `search.py:1391-1413` | 否（处方与顺序不变，只改等待方式） |
| #3 | 并行 `score_path` | `route.py:1580` `_score_path_worker`、`route.py:1587` `plan(..., workers=1)`、`route.py:1613-1625` | 否（报告逐字节相同） |
| #4 | 严格阶段流式预取 | 未采用 | 被 #2 完全覆盖 |

实测（种子 1899537279，45 分钟档，Windows，Python 3.14.7，2026-10-10）：

- **#3**：对 v1 的真实 `seed-facts.json`，`plan(facts, top_n=5)` 串行 `workers=1` 12.095 s → 并行 `workers=6` **2.662 s（4.5×）**，两次报告 `a == b` 为 True，报告哈希同为 `44fc6f4cb662f849`，top ids 同为 `[1,7,2,12,0]`。
- **#1**：v1/v2 的 top-5 = `[1,7,8,9,2]`（7/8/9 逐字节相同）→ 去重后 `[1,7,2,12,0]`，`routes_scored=18`，`alternatives=[8,9,3]`。
- **#2**：strict 阶段墙钟跨度 v1（串行）28.41 s、v2 23.32 s → v3（并发）**11.04 s**（最后一条起始偏移从 +19.25 s 降到 +0.27 s）。

端到端真机复跑（同种子）：

| 运行 | 状态 | 墙钟 s | evaluations |
| --- | --- | --- | --- |
| v1（优化前） | VERIFIED_WIN_IN_NATIVE_HOST | 663.71 | 278 |
| v2（优化前 + 地图释放） | VERIFIED_WIN_IN_NATIVE_HOST | 209.59 | 124 |
| v3（优化后，`route-workers=6`） | VERIFIED_WIN_IN_NATIVE_HOST | 100.20 | 70 |
| v4（优化后，自动检测=1） | VERIFIED_WIN_IN_NATIVE_HOST | 124.26 | 73 |

v4 生效配置：`route_mode=force-then-search`、`route_workers=1`（本机自动检测在 `--route-workers` 未显式给出时对 DP 生效，v4 为对照显式走了默认路径）、`route_top_n=5`、`route_strict=true`、`strict_then_search=true`、`scheduler=weighted`、`clinic=false`、`strategy_prior=off`、`root_policies=[]`；`strict_routes` 5 条 route_id `1/7/2/12/0`、`plan_score` `203.18/203.18/199.24/199.24/185.87`、全部 `NATIVE_ROUTE_DEATH`/`act=0`；`strict_continued.map_released=true`、`search_metrics.route_filtered_branches=47`、`route_released=true`。

> `--route-workers` 的默认值是**自动检测**（`__main__.py:370`，仅当 DP 真跑时才探测）。上表 v3/v4 的差异即并行规划省下的墙钟；v1→v2 的巨大差异来自 `--route-mode` 语义变化，不是本轮优化的收益。

## 五、红线与边界

- **不能宣称胜率提高**。`DP 计划分不能预测路线存活`（Pearson −0.221，交接文档 §18.13）；上表全部是**单种子单次观测**（种子 1899537279 是上游回归种子 TOASTY_MITTENS，不代表分布），四臂布尔结果相同。
- **DP 第 2、3 幕 HP 状态系统性偏低**：幕间回复 `ACT_HEAL_FRACTION=0.8` 按既有裁决**不补**，故 DP 的生存估计不得当作准确值。
- **DP 不替代搜索评估**。strict-then-search 之后丢弃 DP 的卡牌计划与学习先验，只保留（且本轮已进一步释放）地图偏好；启发式仅作分配信号。
- 模拟秒数不是墙钟秒；`release/holdout-01` 属 iteration-055/frozen-i054a，不是 i100 证据，也不是本插件的证据。

## 六、未验证与遗留

- **未重跑 `release/holdout-01`**：`host_sha256` 已在上游合并时变为 `f52cca41a3e8d408950f1f863d8cedab80563754f3b8832fc832f8bd29128998`，其 30 局历史证书不再代表当前实现。
- **未重跑 i100 合成面板**：上游 `release/i100/evidence/sim/*.jsonl` 的 579.1 s / 154-160 基线尚未在本地复现。
- **#3 的并发额外内存未评估**：`score_path` 并行只发生在协调者进程内，未触发 `MemoryGovernor` 的准入检查。
- **未做 guide/force 相对上游 i100 的胜率 A/B**。

## 七、复核命令

```powershell
python tools/run_tests.py
python -m unittest tests.test_dp_modes
python tools/run_solver.py --seed 1899537279 --minutes 45 --config tools/solver_configs/i100-dp-strict-research.json --dry-run
dotnet build native/SpireNativeHost/SpireNativeHost.csproj -c Release -p:Sts2DataDir=<游戏数据目录>
```

## 文档修订说明

- **2026-10-10（首版，deepseek-v4.1-flash）**：新建。记录 DP 插件定位、四模式、strict-then-search 四阶段、四项并行优化与本机实测、红线与未验证项。
