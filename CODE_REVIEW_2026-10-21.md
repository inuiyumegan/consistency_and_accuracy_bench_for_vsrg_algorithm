# CAB-VA 代码审查报告

- 日期：2026-10-21
- 范围：`cab/`（9,500 行）+ `selfcheck/`（5,700 行）+ 构建/配置/测试（约 15,300 行 Python）
- 方式：6 个只读子代理按垂直切片并行审查，主审对高影响项做独立复现；未修改任何源码，未运行完整评测
- 结论：**11 项 P0、48 项 P1、30 项 P2**（去重合并后），其中 6 项 P0 会直接让榜单数字失真（不是崩溃，而是"静默地算错"）

---

## 0. 审查分工与交叉复核

| 子代理 | 切片 | 覆盖文件 | 独有产出 |
|---|---|---|---|
| A | 解析 / 基准加载 / 变异 | `parser/*`、`mutator/*` | 圆形音符误判长键（P0-07）、jitter 和弦去同步 |
| B | 指标与打分数学 | `metrics/*` | NaN 满分链、子档位口径退化、倒挂失明 |
| C | 并发 / 子进程 / 资源 | `runner/suite.py`(1504)、`worker.py`、`config.py` | `pool.map` 永久挂起、terminate 不回收 |
| D | 适配器 / CLI | `adapter/*`、`cli.py`(863) | 失败→0.0、dp/sr 单位混用、select 跨平台 |
| E | 报告 / 导出 / 可视化 | `report/*` | CSV 无 BOM 的完整事故链、破图链接 |
| F | 配置 / 依赖 / selfcheck | `pyproject.toml`、`selfcheck/*` | 打包漏资源、脚本默认写盘 |

主审独立复现的 8 项（后文标 ✅）：

| 验证项 | 结果 |
|---|---|
| 空数据集总分 | **32.30**（ACC 40 / AIN 100 / CT 100 / SP 100 / ASLN 0 / AJ 0）✅ |
| 圆形音符被误判长键 | 856 张中 **1 张受影响、2594 个物件**（`Finixe(zeta) x1.1.osu`）✅ |
| `select()` + `TextIOWrapper` 缓冲 | 缓冲区内已有完整行时 `select` 返回 not-ready ✅ |
| 默认 worker 数 | `min(24, cpu_count)`，本机即 24 ✅ |
| β 常量 | 0.30/0.30/0.20/0.10/0.10 × 0.5 = **15/15/10/5/5%**，与声明一致 ✅ |
| 子档位排除 AIN/CT/SP | 实现与声明一致 ✅ |
| `predictions.csv` BOM | 导出表 `70 61 74`（无 BOM）；`uuzs_bench.csv` `ef bb bf`（有 BOM）✅ |
| 单元测试 | 132 项在 1.31s 内全绿，但 12 个模块零覆盖 ✅ |

---

## 1. P0：会让榜单数字失真 / 让别人跑不起来 / 会毁数据

### P0-01 失败样本被静默剔除，而空集反而拿高分（三处独立路径汇合）

- 位置：`cab/runner/worker.py:33-36,99-106,113-119,139-145`（全部 `except Exception: return None`）→ `cab/runner/suite.py:714-726`（`diff is None` 只从样本池剔除）、`:1229-1232`（SP 同样跳过）、`:916-922`
- 严重度：**P0**｜类型：静默失败 + 分数污染
- 复现：任选一种让算法在扰动谱面上抛异常/返回 NaN（负时间戳、0 长度 LN、超时）。失败样本不进任何计数；若整批失败，`rate_curves` 为空 → AIN=100、CT=100、ACC=40、SP=100，**总分 32.30**（✅ 实测），比不少真实算法还高。
- 影响：**崩溃越多，分数越高**。且 rate/jitter/sln 阶段的失败完全不进 `failed_beatmaps`，事后审计查不出来。部分失败时 RMSE/MAPE 只在存活样本上取均值——算法只在最难的谱面上崩，剩下简单样本让它"变准"。
- 修复：worker 返回结构化 `(ok, value, error)`；父进程按维度统计 `attempted/evaluated/failed`；失败率超阈值（0.5%）或 `evaluated==0` 时该维度置 `None`（未评测），**不得**回退到 0 分或 100 分；`evaluate_*` 空集一律返回 `None`。

### P0-02 NaN 被 `max()/min()` 钳制函数当成满分（四条独立链）

- 位置：`cab/metrics/scoring.py:139-142`、`short_ln_metric.py:105-112`、`jitter_metric.py:93-105`、`speed_metric.py:112-115`、`macro_inversion.py:78-85`
- 严重度：**P0**｜类型：静默 NaN
- 机制：Python 的 `max(0.0, nan)` 返回 `0.0`、`min(100.0, nan)` 返回 `100.0`，于是 NaN 一路拿到最高分。
- 复现（B 实测）：`score_accuracy(rho=1.0, wRMSE=nan)` → **ACC 100**；`short_ln_score(nan)` → **100**；`jitter_score(nan)` → **100**；`calculate_speed_score(nan)` → **100**；6 个 NaN 锚点 → 倒挂罚款被顶到 **30**（同时 `macro_severity=nan` 写进 JSON）。
- 影响：一条 NaN 预测即可抹掉 ACC 的 40 分证据；ND 原生算法直接走到这里，non-ND 算法则在 `PchipInterpolator` 处崩（`calibration.py:243`）。
- 修复：`score_*` 入口统一 `if not math.isfinite(x): return 0.0`（或专用异常）；`evaluate_accuracy` 入口做 `np.isfinite` 掩码；`json.dump(..., allow_nan=False)`。

### P0-03 适配器把"计算失败"变成合法读数 0.0，并以退出码 0 结束

- 位置：`cab/adapter/sunny_cli.py:57-62`、`rosupp_cli.py:112-122`（`print("0.0"); sys.exit(0)`）；`dan_overlay_cli.py:159-164`（`dp is None → res.get("sr", 0.0)`）
- 严重度：**P0**｜类型：假分 / 单位混用
- 复现：`python3 sunny_cli.py bad.osu`（算法抛错）→ stderr 一行 warning + stdout `0.0` + exit 0。Dan-Overlay 的 `parse_error` / `domain_rejected` / `primary_sr_error` 全部返回 `dp=None`，被映射成 0.0（server 仍报 `status: ok`）。
- 影响：该谱面以 ND≈0 进入 ACC 与校准；扰动阶段全 0.0 → AIN/CT/AJ 判为"完全稳定"给满分。若某条路径 `dp=None` 但 `sr` 有值，星数 5–11 会被当成 dan 5–11，**整整错一个量纲**。
- 修复：桥接脚本失败必须非 0 退出且不打印数值；`_parse_numeric_output` 只在 `returncode==0` 且解析出有限值时返回；**禁止**用 `sr` 兜底 `dp`（确有降级需求时显式换算并标记 `degraded`）。

### P0-04 默认 24 worker，且 `--workers` 无任何上下限校验

- 位置：`cab/runner/config.py:153`（`min(24, os.cpu_count() or 4)`）；`cab/cli.py:96-101`（`type=int`，无 choices/无 range）
- 严重度：**P0**｜类型：资源耗尽
- 复现：不带 `--workers` 直接 `run`。本机 24 核/15GB（可用 6GB），默认正好取 24；每 worker 常驻服务 250–300MB → 6–7GB，叠加 numpy 模型副本即 OOM。`--workers 200` 也不会报错。
- 影响：历史 OOM 事故的默认路径依然存在；OOM 之后叠加 P0-05 变成"永久卡死"而非失败退出。
- 修复：默认改 4（或 `min(4, cpu)`）；`type=_positive_int` + 硬上限（8）校验；帮助文案 `default: CPU cores count` 同步更正；`Pool(..., maxtasksperchild=200)`。

### P0-05 任一 worker 异常死亡 → `pool.map` 永久挂起（OOM 后不是报错，是卡死）

- 位置：`cab/runner/suite.py:707,841,880,914,955,1008,1024,1098,1132`（9 处 `pool.map`，无超时、无心跳）
- 严重度：**P0**｜类型：死锁 / fail-hang
- 复现（C 实测）：`Pool(2).map` 内某任务 `os._exit(1)`，8s 后仍是 `timeout 124`，永不返回；`initializer` 抛异常会无限重启 worker，首个 `map` 同样挂死。真实触发：OOM killer、native SIGSEGV。
- 影响：日志停在某个阶段不再前进，`.std_done` 不写，24 个服务进程持续占内存，只能人工 kill。注意 `multiprocessing.Pool` 不像 `ProcessPoolExecutor` 会抛 `BrokenProcessPool`。
- 修复：换 `ProcessPoolExecutor` + `future.result(timeout=...)`；或加看门狗线程（每 60s 检查 `pool._pool` 存活 + 最后结果时间戳），超时 `terminate()` 并把当前维度标 invalid；`map` 外包 `try/finally` 做 terminate+join。

### P0-06 常驻服务 `terminate()` 后不 `wait()`、不杀进程组、无 atexit → 僵尸 + 孙进程残留

- 位置：`cab/adapter/cli.py:244-252`（`_teardown_server`）、`:182-190`（`Popen(..., shell=True, stderr=DEVNULL)`，未建进程组）；全仓库无 `atexit`/`SIGTERM` 处理
- 严重度：**P0**｜类型：进程泄漏 / fd 泄漏 ✅（代码已核）
- 复现：某张谱超时 → `_teardown_server` 只 terminate shell，服务正在 `wine msd.exe` 里则孙进程被 init 收养继续跑；`terminate()` 后不 wait，`/proc/<pid>/stat` 为 `Z`（C 实测）。
- 影响：每个超时残留一个 250–300MB 计算进程继续吃 CPU，下一张又起新的；"内存越跑越少、下一轮 OOM"的直接来源；每次 teardown 留一个僵尸 + 两个 fd。
- 修复：`Popen(..., start_new_session=True)`；teardown 走 `killpg(SIGTERM)` → `wait(5)` → `killpg(SIGKILL)` → `wait()` → 关闭管道；worker 内 `atexit.register` + 显式 SIGTERM handler（spawn 子进程默认不跑 atexit）。

### P0-07 圆形音符的 6 段 hitSample 被误判为长键 endTime，2594 个物件被写坏

- 位置：`cab/parser/osu_parser.py:124-137`（`if len(segs) > 5` 才当作长键，用段数而非 type 位判断）、`:159-163`（`to_line` 回写）
- 严重度：**P0**｜类型：数据损坏 ✅（实测复现）
- 复现：`samples/4k/speed/Finixe(zeta) x1.1.osu` 的 `192,192,3532,5,0,0:0:0:0::`（圆形，type=5）。解析后 `end_time=0, sample_info='0:0:0::'`，序列化回 `...,0:0:0::`（volume 变空串）。
- 实测：**856 张中 1 张、2594 个圆形物件受影响**；`Beatmap.serialize()` 后该谱在 rosu-map 严格解析器上 `volume=""` 解析失败（A 交叉核对 `rosu-map 0.2.1` 源码），即"同一张谱在 rosu-pp 系算法上消失、在宽容解析器上照常计分"。
- 影响：rate/jitter/arrangement/short_ln 四个扰动维度都经 `serialize()`，**每个算法的扰动样本集合会不同**——这正是"一致性基准"最不能出现的失效模式。
- 修复：改用 type 位 `obj_type & HIT_TYPE_HOLD` 判断；圆形分支的 `sample_info` 原样保留；新增 `parse→serialize→parse` **逐字段**相等测试（现有 `tests/test_parser.py:35-44` 只比数量，抓不到）。

### P0-08 部分失败仍 exit 0 → `.std_done` 永久置位 + README 档位样本量被改写

- 位置：`selfcheck/scripts/run_all_standard_benchmarks.sh:75-79`；`cab/runner/suite.py:716-733`（不 raise、不改退出码）；`cab/cli.py:319`；放大点 `generate_scoreboard_tables.py:317`（档位标题用 `sample_count` 取"N 张"）
- 严重度：**P0**｜类型：数据完整性
- 复现：适配器某档大面积崩溃（Wine 起不来/node 缺失）→ `failed_beatmaps` 有记录但只打印前 5 条 → exit 0 → 脚本 `touch .std_done` → 此后批次永久 skip。若第一名那只跑了 156/856 张，README 档位标题会从"856 张"变成"156 张"且无任何报错。
- 修复：`main()` 在失败率 > 1% 时 `sys.exit(3)`；批次脚本额外校验 `sample_count == len(uuzs_bench.csv)`；档位样本量改从数据集 CSV 取。

### P0-09 维护脚本默认"立即写盘 + 扫全仓 `results_*`"，被写目录全在 `.gitignore` 内

- 位置：`selfcheck/tools/recompute_scores.py:685-690,698-704`；同类默认扫描 `resync_md_scores.py:196`、`remove_grade.py:121`、`refresh_report_labels.py:214`、`backfill_weighted_rmse.py:117`、`backfill_domain_profile.py:320`
- 严重度：**P0**｜类型：不可恢复的数据破坏
- 复现：仓库根裸跑 `python3 selfcheck/tools/recompute_scores.py`（无参数）→ 扫描并原地重写 22 个 `results_*` 目录的全部 JSON。`.gitignore:26-27` 忽略 `results_*/`，**写坏没有 git 兜底**；`--dry-run` 只是可选开关。
- 修复：默认 dry-run，`--apply` 才写；写前生成 `.bak` 或整仓快照；默认 roots 限定 `*_standard`；打印"将改动 N 个文件"并要求确认。

### P0-10 submodule 用 SSH URL，且 CI 根本不拉 submodule

- 位置：`.gitmodules:3`（`git@github.com:...`）；`.github/workflows/test.yml:12`（`actions/checkout@v4` 无 `submodules: true`）
- 严重度：**P0**｜类型：环境 / CI
- 复发：无 SSH key 的机器、CI runner、匿名克隆都拿不到 `selfcheck/`；`selfcheck/tests/` 的 13 个守卫测试**从未在 CI 执行**（含专门防"复制权重表"的 `test_weight_table_guard.py`，而同类复制已在 `apply_native_sp.py:31` 发生）。
- 修复：改 HTTPS URL；CI 加 `submodules: true` + 独立 job 跑 `pytest selfcheck/tests`。

### P0-11 `normalize_sample_od.py --apply` 第二次运行会用表头覆盖回滚清单

- 位置：`selfcheck/scripts/normalize_sample_od.py:160-162`（`old == new_s` 时 `continue`，manifest 保持空）+ `:174`（无条件 `open("w")`）
- 严重度：**P0**｜类型：数据丢失
- 复现：连续两次 `--apply` → `samples/4k/od_manifest_before_normalization.csv` 变成只有表头，`--revert` 立即失效。同目录的 `ingest_new_charts.py:39-42` 已在 docstring 里批评并修复了这个问题，旧脚本仍在原地可用。
- 修复：沿用 `merge_manifest()`（读旧+合并+排序）；`if manifest:` 才写。

---

## 2. P1：逻辑偏离设计 / 口径不一致 / 静默降级

### 2.1 评分口径（影响名次，优先处理）

| # | 问题 | 位置 | 复现与影响 | 修复 |
|---|---|---|---|---|
| P1-01 | **子档位 ASLN/AJ 退回 legacy 口径**，与全域段不是同一把尺 | `cab/runner/suite.py:608-622`（子档）vs `:1055-1061,1169-1177`（全域） | 子档调用 `evaluate_jitter/short_ln` 时不传 `rstar_median` → 走 MAPE / ITR 7% legacy 曲线。真实证据：MinaCalc greek_dan `rstar=null`、AJ=27.2；同算法 all `rstar=1.04439`、AJ=39.5 | 子档内用该档子集重算 r*；或在报告/JSON 显式标注"子档为 legacy 口径" |
| P1-02 | **缺失维度有三套策略**，且与注释自相矛盾 | `cab/metrics/scoring.py:254-309` | `inversion/continuity=None`→不折扣；`speed=None`→75 分；`short_ln/jitter=None`→**0 分**（扣 15%/5%）。注释写的是"None 不贡献折扣"。实测 ACC=100 其余全 None 的子档总分 80.75 | None 一律"未评测、不折扣"；删除 `speed` 的 75 分默认值 |
| P1-03 | `min_cohort_size` 是**死参数**，单样本档位也被评分 | `cab/metrics/calibration.py:126-127,161-166`；`suite.py:571` | `grep` 全仓无读取；`count==0` 是唯一门槛，1 张谱也能成为锚点并驱动 PCHIP 与倒挂罚款。单样本 ACC=40、总分 32.3 | `count < min_cohort_size` 跳过锚点；档位样本 < 3 判 out_of_domain |
| P1-04 | **结构倒挂对 2 个锚点完全失明**，短序列被系统性低估 | `cab/metrics/macro_inversion.py:50-61,78-85` | `{1:9, 2:1}`（全倒挂）→ S=0、罚 0；`[5,4,3]` → S=0.25（k=3/5 记 0 却仍除以 3） | scale 仅在可评估时进入平均；len=2 单独定义相邻 dip |
| P1-05 | SP 的 overhead 灰区（0.25×~1.0×）把耗时压成 0.001ms → SP≈100 | `cab/metrics/speed_metric.py:171-176` | `evaluate_speed([(500ms,500s)], overhead=1000)` → 0.0 ms/s、100 分；带 baseline 时 98.76。docstring 说"不超过 overhead 的样本不动"，实际只有 ≤25% 才不动 | `t_ms <= overhead` 一律原样返回；或与 `t_ms*0.75` 取 max |
| P1-06 | SP 相对公式在 ρ→0 的极限被写成 0 分 | `cab/metrics/speed_metric.py:97-99` | `relative(1e-9)=99.95` 但 `relative(0)=0.0`；与 `calculate_speed_score(0)=100` 结论相反 | `ratio<=0` 且有限时返回 100，仅 NaN/Inf 返回 0 |
| P1-07 | 家族权重按"在场家族"重归一，与 README 公式不符 | `cab/metrics/accuracy.py:141-146` | `balanced_stratified_rmse({'ln':2.0})` → 2.0（ln 权重变 100%）；过滤/quick 模式下 ACC 不可与全量比较 | 明确一种口径并写进 README，或保留绝对权重 |
| P1-08 | 总分无法由已发布的 2 位小数维度复算 | `scoring.py:353-359`、`json_export.py:141` | 243 份历史 JSON 中 **66 份**重算差 ±0.01；外部复算出现假 diff | 同时存原始精度（`*_raw`）或统一"先 round 再乘" |
| P1-09 | 单档位 out-of-domain 显示 `0.0/100` 而非 N/A | `cab/report/markdown.py:203-216` | N/A 分支被嵌在 `if is_subtier` 内；过滤到 0 样本的单档运行被打 0.0 分 | N/A 判断提到 subtier 之外；JSON 输出 `null` |

### 2.2 并发与子进程

| # | 问题 | 位置 | 复现与影响 | 修复 |
|---|---|---|---|---|
| P1-10 | **`select()` 作用在 `TextIOWrapper` 上 → 已缓冲行被判超时** | `cab/adapter/cli.py:194-212` | ✅ 实测：一次 flush 两行时，`readline` 取走第一行后 `select` 返回 not-ready → 等满 30s 判超时 → teardown + one-shot 重算。3 次后 `_server_failures>=3`，该 worker **永久**退回 one-shot（Dan-Overlay 单图 11ms→5s），且无任何日志 | 读线程 + `queue.get(timeout=)`，或二进制管道自行按 `\n` 切分；降级要 `logging.warning` |
| P1-11 | Windows 上常驻模式必然不可用 | `cab/adapter/cli.py:204` | Windows 的 `select` 只接受 socket，对匿名管道抛 `WinError 10038` → 计入失败 → 3 次后永久降级 | `os.name=="nt"` 走读线程；或禁用并告警 |
| P1-12 | 常驻服务 `stderr=DEVNULL`，启动失败零诊断 | `cli.py:187` | 路径含空格/解释器错/import 失败 → 子进程秒退，只有 EOF，无 stderr | 重定向到日志文件或继承父 stderr；失败时附最后 N 行 |
| P1-13 | rosu-pp 包装器**无条件改写响应 id**，抹掉唯一的串包检测 | `cab/adapter/rosupp_cli.py:85-86` | 残留旧响应会被贴上新 id 返回，`CliAdapter` 的 desync 检测被提前消灭 → 错谱配错分 | 校验 `resp["id"]==req_id`，不匹配则重启 runner；不要改写 id |
| P1-14 | `--rate-step 0` 或负数 → 父进程死循环 | `cab/cli.py:102-107`；`suite.py:292-299,865-870` | `while r <= max: r += 0` 永真；100% CPU、无输出、必须 kill | argparse `type=_positive_float` 且 `<=0.5`；while 加迭代上限 |
| P1-15 | SP 探针中超时/失败的马拉松被剔除 → SP 偏高 | `suite.py:1227-1232` | 慢算法因超时被剔除而"变快"；全失败时回退到受并发污染的 `acc_timings`（注释自承 4 vs 8 worker 差 26%） | 记录 `sp_attempted/sp_ok`，覆盖率 < 90% 时 SP 置 None；marathon 单独放大 timeout |
| P1-16 | 无 per-task 超时，in-process 的 Daniel 一张病态图挂死整轮 | `suite.py:707` 等；`adapter/daniel.py:118-129` | 父进程无法中断 worker 内计算 | `apply_async().get(timeout=)` 逐任务取；或 `faulthandler` + `os._exit` 让池 fail-fast |
| P1-17 | initializer 抛异常 → 无限重启 worker → 首个 map 挂起 | `suite.py:689-693`；`worker.py:17-25`；`adapter/daniel.py:104-105` | 换机器/缺依赖时表现"启动后永远不动"，traceback 只在 worker stderr | 提交任务前做一次 init 健康检查；连续 N 次 worker 退出即 abort |
| P1-18 | 结果目录无 `.running` 标记、无 run_id，中断后旧产物冒充新结果 | `cab/cli.py:764` 之后；产物均 `"w"` 覆盖 | Ctrl-C 后旧 `cab_benchmark_result.json` 原地不动 | 开跑写 `.running`（pid/时间/参数），成功才写 `.std_done`；报告回显 run_id |

### 2.3 适配器与 CLI

| # | 问题 | 位置 | 复现与影响 | 修复 |
|---|---|---|---|---|
| P1-19 | `--scale` 与算法不匹配完全无校验，等于隐藏的"关闭校准"开关 | `cab/cli.py:78-83,553`；`calibration.py:195-201` | `-a sunny --scale nd` 直接用 SR(5–11) 对 ND(10–18)，ACC 必崩；`-a sunny --scale daniel` 错减 0.5 | 维护 `ALGO_DEFAULT_SCALE`；显式覆盖时打印"已关闭默认校准"警告 |
| P1-20 | `--sp-only` 在 0 张成功时仍写 100 分档案；`--sp-source` 不校验身份 | `cab/cli.py:650-653,711-755`；`suite.py:196-259` | `evaluate_speed([])` = 100 → 写档案 exit 0 → 后续继承该 SP（+5 总分） | `evaluated_count==0` 时非 0 退出且不写；加载时校验 `algorithm` 与 `evaluated_count` |
| P1-21 | `--cmd` 模板用 `.format()`，含 `{}` 直接崩 | `cab/adapter/cli.py:82-83` | `--cmd 'algo --json {"k":1} --file {input}'` → `KeyError '"k"'` | `template.replace("{input}", quoted)` |
| P1-22 | `shell=True` + 路径未引号化（20+ 处） | `cli.py:360-362` 等；`adapter/cli.py:87-93` | 路径含空格即拆词（Windows `C:\Users\John Doe\` 必炸）；`shlex.quote` 是 POSIX 语义，Windows 无效 | 全链路 list argv；Windows 用 `list2cmdline` |
| P1-23 | `--predictions-file` 的目录部分被丢弃 | `cab/cli.py:791,826,847`（`Path(...).name`） | `--predictions-file out/sub/x.csv` 实际写到 `<output-dir>/x.csv` | `out_dir / args.predictions_file` + mkdir |
| P1-24 | `--no-export-predictions` 连带关闭 probe ledger | `cab/cli.py:788-794,825-830,846-849` | 只想关 CSV，rate/jitter/sln ledger 也没了，事后无法审计 | 拆成 `--no-export-ledgers` |
| P1-25 | 输出目录只在跑完后创建 | `cab/cli.py:767-768` | `-o` 指向不可写路径 → 数小时评测在最后一步 `PermissionError` | 启动时 mkdir + 写权限探测 |
| P1-26 | `--daniel-path` 相对 CWD，包内 fallback 走不到 | `cab/cli.py:55-58`；`adapter/daniel.py:96-106` | `pip install -e .` 后在别的目录跑 → `FileNotFoundError` | 相对路径先 CWD 再 `__file__` 回退 |

### 2.4 解析与变异

| # | 问题 | 位置 | 复现与影响 | 修复 |
|---|---|---|---|---|
| P1-27 | `Beatmap.parse("不存在路径")` 静默返回空谱面 | `osu_parser.py:209-215` | str 入参静默吞（Path 入参才抛）；空谱面难度 0 或以 None 进入 ACC | 显式 `parse_content()`；`.osu` 后缀不存在即 `FileNotFoundError` |
| P1-28 | 缺 `[HitObjects]/[TimingPoints]` 静默空谱面，`duration_seconds` 兜底 1.0 | `osu_parser.py:196-202,271-282` | 404 HTML / 截断文件都会走到这里；1.0 秒分母把 ms/s 放大几个数量级 | 完整性校验并 raise；`duration_seconds` 不兜底 |
| P1-29 | loader 对"定位不到文件"的行静默丢弃 | `bench_loader.py:272-282` | ✅ 实测：`samples_root` 传错 → 856 变 **845**，零告警 | 返回 `missing_rows`；suite 入口 fail-fast |
| P1-30 | 长键第 6 段不足 6 段 → 静默"0 长度长键" | `osu_parser.py:105,124-137,85-88` | `128,0,0:0:0:0:` → `is_ln=True, duration=0`；`max(0,...)` 把负时长也伪装成 0 | 用 type 位判断；`end_time<time` 显式 warn/raise |
| P1-31 | jitter 逐音独立抖动**破坏和弦同时性** | `mutator/jitter.py:100-126` | ✅ 实测：Bookmaker 1863 个和弦中 454 个（24%）展宽 >10ms，p95=15ms。pattern-aware 算法会对 flam 真实响应，却被计入"时间抖动鲁棒性" | 同 `time` 物件共用同一 delta；或在 docstring 明确"同时探测量化噪声+和弦去同步" |
| P1-32 | jitter 可产生负时间戳 | `mutator/jitter.py:118-124` | ✅ 实测：49 张首音 <50ms 的谱中 10 张出现负时间（最小 −8ms） | `max(0, ...)` + `serialize()` 前断言 |
| P1-33 | 未校验 `Mode`/`CircleSize`，7K 谱会被当 4K 计分 | `bench_loader.py:272-282`；`osu_parser.py:181-187,73-74` | 删掉 `CircleSize` → x=36/109 都落第 0 列 → 凭空"假 jack" | loader 断言 `Mode==3 && CircleSize==4` |

### 2.5 报告与数据产出

| # | 问题 | 位置 | 复现与影响 | 修复 |
|---|---|---|---|---|
| P1-34 | `predictions.csv` / 三个 ledger**均为 UTF-8 无 BOM** | `report/predictions_csv.py:201`；`probe_ledger.py:91` | ✅ 实测 111 个导出 CSV 全无 BOM，而 `uuzs_bench.csv` 有 BOM。WPS 打开保存一次 → `定时地狱`→`瀹氭椂鍦扮嫳` → `refresh_isacc` 的 `path` 全部不命中、ISACC 静默变空 | 改 `utf-8-sig`；**同时**改 `tests/test_predictions_csv.py:155,181`（它们把无 BOM 钉死了） |
| P1-35 | `annotate_bench_csv_paths.py` 读时吃 BOM、写时不还原 —— 事故真正来源 | `selfcheck/scripts/annotate_bench_csv_paths.py:98→164` | 对带 BOM 的基准表跑一次即永久丢 BOM（`:106` 还专门 `lstrip("\ufeff")`，说明知道 BOM 存在） | 写回 `utf-8-sig`；写前断言保留原样 |
| P1-36 | 基准表加载对乱码"静默容错" | `bench_loader.py:200`（`errors="replace"`） | GBK 存档被读成 U+FFFD，`float(expected)` 仍通过 → 坏数据进评测 | `errors="strict"` + UTF-8 可解码性检查 + 明确报错文案 |
| P1-37 | 校准图从不生成，报告照样插图片链接（10 处破图） | `visualization.py:47-50` vs `:133-134` | Mixed（`is_nd=True`）的 5 份报告 × 2 语言 = **10 条** `cab_calibration_curve.png` 链接指向不存在的文件；`cli.py:856` 还打印"Chart (calibration) saved" | plot 函数返回 `Optional[Path]`，只有 savefig 成功才登记 |
| P1-38 | JSON 允许 NaN/Infinity，`realtime_factor=inf` 可达 | `json_export.py:315,396`；`speed_metric.py:182` | 空 timings → `inf` → 写出裸 `Infinity` 令牌；JS/Go/jq 全部解析失败（Python 自己能读，故本地 CI 抓不到） | `allow_nan=False` + 非有限值映射 `None` |
| P1-39 | README 速度列 4 个硬编码，3 个已漂移 | `generate_scoreboard_tables.py:413-418`（`RAW_SPEED_OVERRIDE`） | Arily 0.0975 vs JSON avg 0.2839 / median 0.193；MinaCalc 0.0129 vs 0.0577；lazer 0.1324 vs 0.2636。README:105 宣称"取 median"，两个值都不是 median | 删 override；确需覆盖则落 `source/why/machine` 字段并在生成器读取 |
| P1-40 | `apply_readme_tables.py` 当前必然 `SystemExit`，README 只能手改 | `apply_readme_tables.py:36`（ORDER 仅 **11** 项）`:95-96` | ✅ 实测 ORDER=11，而 README raw 表 15 行 → `table has 15 rows, generator produced 11` 后退出；因写盘在 `:125` 之后，不会留半张表 | 删 `ORDER/raw_rows`，直接消费生成器的 `E. RAW METRICS TABLE` |
| P1-41 | `resync_md_scores` 会把子档位 4 列评分卡改写成 7 列 | `resync_md_scores.py:57-64,210-216` | dry-run 实测：`| 15.2/100 | 18.4 | - | 0.0 | - | - | 35.7 |` 塞进 4 列表头 → Markdown 错位 | 按 `is_subtier` 分支输出 4 列，或就地替换单元格 |
| P1-42 | `refresh_isacc.py --check` 漏检三类漂移 | `refresh_isacc.py:100-104` | 旧有新无、新有旧无、`n`/`scope_text` 变化都不判 drift；`:70-72` 静默丢弃 path 未命中的行 | `set(old)^set(new)` 计入 drift；比较 `(score,n,scope_text)` 三元组 |
| P1-43 | 打包漏掉非 `.py` 资源，`pip install cab-va` 后 5 个适配器不可用 | `pyproject.toml:25-27`；`cab/cli.py:357,369,399,467,476` | `cab_va.egg-info/SOURCES.txt` 中 `cab/` 下无任何非 `.py` 文件；`node: cannot find estimator_cli.mjs` | 加 `package-data`，或资源迁 `cab/_data/` + `importlib.resources`；CI 构建 wheel 并断言 |

### 2.6 工程与文档

| # | 问题 | 位置 | 影响 | 修复 |
|---|---|---|---|---|
| P1-44 | 散点页写死"832 张"，数据集已 856 | `generate_scatter_pages.py:125` | `charts/*.html` 给读者错误样本量；`ingest_new_charts --check` 不含此文件，永远绿灯 | 从 CSV 行数注入 |
| P1-45 | "声明域"三处实现，Companella 与"排除 LN"语义不一致 | `generate_scoreboard_tables.py:73`（权威）/`generate_scatter_pages.py:56-60`（`ND<=15.5` 且漏 `ln course`） | 散点图与榜单 ISACC 不是同一个域，而 README:96 宣示同口径 | 抽单一 `declared_scope.py`，三处 import；加断言三处 n 一致 |
| P1-46 | 硬编码家目录绝对路径 + 模块级执行 | `generate_all_algorithms_comparison.py:23` | `/home/inui/.gemini/...`，他人运行在自己 HOME 建垃圾目录；`import` 即执行 | `--out` 参数 + `main()` 守卫 |
| P1-47 | `pandas` 声明但主包零使用；无 LICENSE 文件 | `pyproject.toml:14`、仓库根 | 多装 ~50MB + pandas 3.0 破坏性变更进依赖图；公开发布 856 张第三方谱面但项目自身许可未定义 | 移除 pandas（selfcheck 单独 requirements）；补 LICENSE + `license` 字段 |
| P1-48 | selfcheck 依赖主包私有符号 | `backfill_domain_profile.py:42`、`measure_native_sp.py:36` | `markdown.py` 重构即 ImportError，无测试守护 | 提升为公开 API，或加 import 冒烟测试 |

---

## 3. P2：健壮性（择要，完整清单见各切片原始报告）

| # | 问题 | 位置 | 影响 |
|---|---|---|---|
| P2-01 | `stdout` 为空 → `splitlines()[-1]` 抛 `IndexError`；`float()` 的 ValueError 未捕获 | `adapter/cli.py:114-116` | 真实 stderr 被 "list index out of range" 掩盖 |
| P2-02 | BOM 导致版本行丢失：49 张谱（38×v128）被降级为 v14 | `osu_parser.py:222-223,168` | 经 serialize 的变异谱格式信息损坏 |
| P2-03 | `TimingPoint.from_line` 的 int 字段过严，`4.0` 即整条 BPM/SV 点丢失 | `osu_parser.py:43-51` | 静默丢节拍信息（当前数据未触发） |
| P2-04 | `serialize()` 只回写白名单 section，`[Colours]` 在 59/856 张谱中被丢弃 | `osu_parser.py:294-326` | 非忠实往返，干扰文本 diff 排查 |
| P2-05 | loader 模糊回退可能把行配到**错误文件** | `bench_loader.py:256-270` | 36 行只能靠 `startswith(name[:15])` 命中，无唯一性校验 |
| P2-06 | `expected` 无有限性校验，NaN/inf 会被接受 | `bench_loader.py:210-213` | NaN 让所有 `>=/<=` 过滤恒 False，样本静默消失 |
| P2-07 | CSV 读取未用 `newline=""`（测试里用了） | `bench_loader.py:200` | 引号内含换行时错行 |
| P2-08 | 变异器参数无校验（`max_ms=-5` 会把所有音符后移 5ms） | `mutator/jitter.py:61-73`、`short_ln.py:71` | 静默产出畸形谱 |
| P2-09 | 重复行运行时不去重（只有测试守 `uuzs_bench.csv`） | `bench_loader.py:199-284` | `--csv` 传入重复 path 会被计两次权重 |
| P2-10 | 失败点被删后曲线出现"大跨度 step"，倒挂/悬崖按一个 step 算 | `suite.py:846-848`；`metrics/inversion.py:98-107` | 1.2x 失败会让 1.1→1.3 变成 0.2 宽 step，凭空造出/抹掉倒挂 |
| P2-11 | `rate_curves`/`jitter_map`/`sln_map` 以 `name` 为键 | `suite.py:786-788` 等 | 同名谱会共享曲线（当前无重名，属潜在） |
| P2-12 | `measure_platform_overhead_ms` 协议失配后不 teardown | `adapter/cli.py:137-158` | 失败传染下一次计算，逼近 3-strike 永久降级 |
| P2-13 | `/dev/shm` 临时文件无启动清扫 | `adapter/in_memory.py:47-61` | SIGKILL 后残留（`/dev/shm` 计入内存） |
| P2-14 | 扰动阶段父进程侧 `open()` 无 try，跑几小时后中止且无部分结果 | `suite.py:805-807,1128` | I/O 错误全盘重置 |
| P2-15 | 所有产物原地直写，无原子写 | `predictions_csv.py:201`、`json_export.py:315` 等 | Ctrl-C/磁盘满留下半截文件，下游"能解析多少用多少" |
| P2-16 | 绘图失败被 `print` 吞，旧 PNG 留在原地被新报告引用 | `visualization.py:39-69` | 复用 `--output-dir` 时链接到上一轮另一算法的图 |
| P2-17 | Rich 控制台标记注入 | `report/console.py:43-64` | 算法名含 `[beta]` 被吞字；含 `[/ b]` 抛 `MarkupError` 中断收尾 |
| P2-18 | `failed_beatmaps` 表格单元格未做 Markdown 转义 | `markdown.py:650,902` | 报错含 `\|` 时整表列错位 |
| P2-19 | 两个 CSV 的 `nan`/`-0`/精度口径不一致 | `predictions_csv.py:88-98` vs `probe_ledger.py:61-70` | 同批产物 `nan` vs `NaN`；`got < 5e-5` 被写成 `0` |
| P2-20 | `_relativize` 在基准树之外输出绝对路径 | `predictions_csv.py:101-113` | 破坏"可与 `uuzs_bench.csv` join"的承诺 |
| P2-21 | `sp_sources/` 默认路径指向被 gitignore 的目录 | `cab/cli.py:543-550`；`.gitignore:87` | 新克隆里只打一行 `[!]` 然后继续 → sunny-rs 的 SP 继承静默失效 |
| P2-22 | `recompute_scores` 覆盖后销毁 `_previous` 审计痕迹 | `recompute_scores.py:434-438,513` | 唯一能改榜单分数的工具没有历史记录 |
| P2-23 | 缺字段被写成 `has_native_immunity: true` | `recompute_scores.py:493-496` | 老 JSON 无 `max_absolute_delta` → 对算法能力的事实性错误声明 |
| P2-24 | 多档位子目录永不刷新 AJ | `recompute_scores.py:605-611` | 子档 AJ 保持旧值（同类事故已发生过一次） |
| P2-25 | `cab_run.sh` 裸 `python3`，无解释器自检 | `cab_run.sh:2` | 命中无 numpy 的解释器 → 报错指向 `cab/__init__`，难判断 |
| P2-26 | 默认路径与测试路径都相对 CWD，无 `conftest.py` | `cab/cli.py:92,111,57`；tests 各处 | `cd /tmp && pytest /repo/tests` → 约 40% 用例静默 skip |
| P2-27 | `normalize_sample_od`/`ingest_new_charts` 先写后校验，失败不回滚 | `:163-168` / `:167-171` | 宣称的"除 OD 外逐字节相同"在失败时是假的 |
| P2-28 | `split_reform_intros.py` 静默覆盖被追踪的 `.osu`，用 `assert` 做唯一性守卫 | `:55,157` | `python -O` 下守卫失效 |
| P2-29 | 榜单生成器把缺失维度渲染成 `0.000` | `generate_scoreboard_tables.py:437-445` | 读者理解为"该维度最差"，正确语义是"未测量" |
| P2-30 | 两个工具对同一 JSON 写出不同字节（尾换行） | `backfill_weighted_rmse.py:48-53` vs `remove_grade.py:100-102` | 互相"发现差异"，幂等断言假失败 |

---

## 4. P3：质量与一致性（合并清单）

- **死代码**：`adapter/daniel_cli.py` 全仓无引用；`BaseAdapter.calculate_batch` 无人调用；`daniel.py:23-48` 复制上游常量表（上游一改就静默漂移）；`--is_base_algo` 含不存在的名字（`minacalc 515`）。
- **拼写**：`"non-ng"` 出现在 `cab/cli.py:688`、`suite.py:1260`（当前恰好落进 non-nd 分支，无功能损害）。
- **舍入**：`int(round(x))` 是银行家舍入（halves-to-even），而 osu! 量化是 `floor(x+0.5)`；`inversion.py:73` 又用 1ms 差做噪声底噪估计 → 引入 0.5ms 非对称成分。
- **`serialize()` 原地排序副作用**（`osu_parser.py:321`）：`BenchmarkSample` 缓存 `_beatmap`，任何调用方 serialize 都会改到共享状态。
- **注释过期**：`scoring.py:4-12` 还写着"ACC 50%、加权和"；`tests/test_metrics.py:170-177` 注释与调用值不符。
- **文档漂移**：`--workers` help 说 "CPU cores count"；`--algorithm` help 只列 2 个实际支持 13+ 别名；`--mode full` "100 rate steps" 实为 103–111；`--predictions-file` 说可带路径实际丢目录；指南 §5.5 承诺分档报告也列 Excluded，实际只 master 有。
- **抗短面叙述脱节**：`markdown.py:502` 仍写"目标 ITR 7%"，实际 ASLN 已按 r* 计分；`immunity_grade` 仍走 ITR 口径，与分数并列展示。
- **CJK 字体未设**（`visualization.py:9-15`）：当前图内全英文安全，但图例嵌了 `algorithm_name`/`name[:25]`，出现中文名即方框。
- **i18n 泄漏**：`json_export.py:119,342` 的 `tier_name` 默认中文写进机器可读 JSON。
- **测试风格**：`unittest.TestCase`（10 文件）与 pytest 函数式（2 文件）混用。
- **`results/`（根目录）是旧加权和产物**：`score_summary.weights={ACC:0.4,...}` 且 `total=83.14 > ACC=81.59`，与当前乘性口径完全不同，任何脚本误读即得出错误榜单——建议直接删除或加 `_archive` 前缀。

---

## 5. 确认无问题（避免过度修改）

这些点经专门核查，**不要动**：

1. `pool.map` 保序，`zip(samples, outputs)` 不会错位；父进程独占聚合，无共享可变状态、无需锁。
2. 所有内置常驻服务都是 stdio JSON，不监听端口，每 worker 私有，**不存在**"多 worker 抢端口串台"；协议有自增 id 校验。
3. 随机种子全程显式传递（`RandomState(seed)`、jitter/sln 每任务传 seed），与调度无关，可复现。
4. β 常量（15/15/10/5/5%）与"子档位 = ACC×ASLN×AJ、SP/AIN/CT 不参与"的实现与声明一致。
5. 各子分都做了 `[0,100]` 钳制，总分为 `ACC × ≤1`，不会越界（问题在于钳制掩盖了 NaN/空集）。
6. `cab/report/**` 的所有 `open()` 都显式带 `encoding=`，**不存在**依赖 locale 的崩溃风险；唯一例外是 `bench_loader.py:167` 的二进制嗅探。
7. `predictions_csv.py` / `probe_ledger.py` 一律 `"w"` 覆盖写，重复运行不会翻倍/追加。
8. `visualization.py` 强制 `Agg` 后端且每次 `plt.close(fig)`，无头 CI 安全。
9. `cab/cli.py:33-36` 用 `sys.executable` 而非硬编码 `python3` 拉起 adapter shim，Windows 场景考虑周到。
10. `ingest_new_charts.py` 是全目录唯一"防误操作"完整的脚本（默认 dry-run、`--check`、plan/apply、manifest 合并、integrity check），可作为其它脚本重构模板。
11. Python 版本兼容性干净：`ast.parse(feature_version=(3,10))` 全量通过；`np.float_`/`df.append`/`tomllib`/`ExceptionGroup` 等 0 命中。

---

## 6. 修复路线图

### 第 1 批：立即（会让已发布榜单不可信的 6 项）

1. **P0-01 + P0-02**：NaN/空集一律判"未评测"，禁止回退到 0 或 100。这是唯一同时影响所有维度、且能被一条 NaN 触发的缺陷。
2. **P0-03**：适配器失败必须非 0 退出，删除 `sunny/rosupp` 的 `print("0.0")`，删除 Dan-Overlay 的 `sr` 兜底。
3. **P0-07**：改用 type 位判长键 + 加 `parse→serialize→parse` 逐字段往返测试。
4. **P0-08**：`main()` 失败率 > 1% 时 `sys.exit(3)`；档位样本量改从 CSV 取。
5. **P0-04 + P0-05**：默认 worker 改 4 + 上限校验；`pool.map` 换 `ProcessPoolExecutor`（或加看门狗）。
6. **P0-06**：teardown 走 `killpg → wait → killpg(SIGKILL) → wait`，worker 内注册 atexit/SIGTERM。

> 建议顺序执行并在每步后重跑 `pytest tests -q`（当前 132 项 1.31s，回归成本极低）。第 1 批完成后**必须重跑全部 11 个算法**——P0-01/02/03 会改变既有分数。

### 第 2 批：本周（口径一致性 + 他人可用性）

7. **P1-01**：子档位 r* 口径对齐（影响子档名次，需重新生成子档表）。
8. **P0-10 + P1-43**：submodule 改 HTTPS + CI 拉 submodule + `package-data` 补 `.mjs`。
9. **P1-34 + P1-35**：CSV 统一 `utf-8-sig`（含同步改测试断言），修 `annotate_bench_csv_paths.py` 的 BOM 回写。
10. **P1-39/40/41**：删 `RAW_SPEED_OVERRIDE`、`apply_readme_tables` 复用生成器、`resync_md_scores` 按 subtier 分支。
11. **P1-10/12**：常驻服务读线程替换 select、stderr 落日志。

### 第 3 批：随后（健壮性与工程化）

12. P1-02/03/04 的评分默认值与前门限；P1-05/06 的 SP 边界；P1-13 的 id 校验。
13. CLI 校验族（P1-14/19/20/21/23/24/25）：成本低、收益直接。
14. P2 系列 + 补 12 个零测试模块的最小回归（优先 `cli.py`、`worker.py`、`probe_ledger.py`、5 个算法适配器）。

### 建议新增的 8 个回归测试（按性价比排序）

1. `test_roundtrip_field_fidelity`：856 张 `parse→serialize→parse` 逐字段相等。
2. `test_no_data_is_not_perfect`：空集时 AIN/CT/SP/ACC 必须是 `None`/未评测，总分不得为正。
3. `test_nan_rejected`：NaN 进入 `evaluate_*` / `score_*` 必须报错或 0，不得满分。
4. `test_adapter_failure_exits_nonzero`：假 server 返回 error / 空 stdout / `NaN` / 多行输出。
5. `test_loader_reports_unresolved_rows`：错 root 必须报错而非静默 845。
6. `test_subtier_uses_same_rstar_scale`：同算法子档 AJ 与全域口径一致。
7. `test_jitter_preserves_chord_sync` 与 `test_jitter_no_negative_time`。
8. `test_predictions_csv_has_bom` + `test_json_has_no_nan`。

---

## 7. 一句话总结

这套基准的**数学设计比工程实现严谨得多**：声明口径（乘性折扣、β 值、子档位排除项、子键型 3:1 加权）逐条核对下来基本都落实了，测试断言质量也真实可信；真正的系统性风险集中在同一个模式——**"静默"**：失败静默变 0 分或满分、解析失败静默返回空、找不到文件静默跳过、口径不一致静默并存。对一致性基准而言，静默比崩溃危险得多，因为它改变的是每个算法的样本集合，而报告上看不出来。

---

## 8. 实施记录（2026-10-21 修复）

### 8.1 验证状态

- `pytest tests -q`：**155 passed**（原 132 + 新增 `tests/test_review_regressions.py` 23 项）
- `python3 -m compileall cab selfcheck/scripts selfcheck/tools`：0 错
- 端到端小规模实跑（Daniel，4 张 accuracy + 2 张扰动，`--workers 2`）：跑通，总分可从上榜的 2 位小数维度**精确复算**（48.06 == 48.06）
- 全产物写出验证：JSON 可严格解析、`predictions.csv` 带 BOM、图表只登记真实存在的文件

### 8.2 已修复清单

| 组 | 内容 | 主要落点 |
|---|---|---|
| P0-01/02 | 空集与 NaN 不再产出分数：`is_evaluated` + 各维 `None` | `metrics/*`、`suite.py`、`json_export.py`、`markdown.py` |
| P0-03 | 适配器失败非 0 退出；`dp=None` 不再用 `sr` 兜底；NaN/inf/空输出一律报错 | `sunny_cli.py`、`rosupp_cli.py`、`dan_overlay_cli.py`、`adapter/cli.py` |
| P0-04/05 | 默认 4 worker + 1..64 校验；`_map` 逐任务超时，worker 暴毙转为报错 | `config.py`、`suite.py` |
| P0-06 | 服务进程组化 + `killpg → wait → SIGKILL → wait` + worker 内 atexit/SIGTERM | `adapter/cli.py`、`worker.py` |
| P0-07 | 长键改用 type 位判定；圆形 hitSample 原样保留 | `osu_parser.py` |
| P0-08 | 失败率门禁、`sys.exit(3)`、`.std_done` 复核产物、档位样本量取最大值 | `cli.py`、`run_all_standard_benchmarks.sh`、`generate_scoreboard_tables.py` |
| P0-09 | 维护脚本改为**原子写 + 首次 `.bak` 快照**（保留"直接生效"的工作流，见 8.4） | `recompute_scores.py` |
| P0-11 | OD manifest 改为读旧+合并+排序+原子替换 | `normalize_sample_od.py` |
| P1-01 | 子档 r\* 由该档子集的 rate 曲线现算，不再退回 legacy ITR/MAPE | `suite.py::_evaluate_tier_slice` |
| P1-02~09 | 缺失维度统一"未评测"、`min_cohort_size` 生效、倒挂短序列、SP 边界、家族权重重归一说明、round-then-multiply | `scoring.py`、`calibration.py`、`macro_inversion.py`、`speed_metric.py` |
| P1-10~18 | 读线程替换 select、Windows 可用、stderr 保留、id 校验、rate-step 校验、SP 覆盖率、per-task 超时、init 健康检查、run_id/.running | `adapter/cli.py`、`rosupp_cli.py`、`suite.py`、`cli.py` |
| P1-19~26 | scale 覆盖告警、sp-only 空档案门禁、`.format`→`replace`、argv 化、预测路径、ledger 独立开关、输出目录预建 | `cli.py`、`adapter/cli.py` |
| P1-27~33 | 空谱面/缺 section 显式报错、loader 未解析行、长键残缺、负时间、Mode/CircleSize 准入 | `osu_parser.py`、`bench_loader.py`、`mutator/jitter.py` |
| P1-34~43 | CSV BOM、annotate 保留 BOM、JSON `allow_nan=False`、断图、速度列单一来源、README 表格单一来源、子档评分卡列数、ISACC 漂移、package-data | `report/*`、`selfcheck/*`、`pyproject.toml` |
| P1-44~48 | 散点页张数与声明域、硬编码路径、pandas 移除、私有符号 | `generate_scatter_pages.py`、`generate_all_algorithms_comparison.py`、`pyproject.toml` |
| P2 | 30 项中 **24 项已修**（详见 8.4） | 全仓 |
| P3 | `non-ng` → `non-nd`（含 Disclaimer 的 "ng 难度" → "ND 难度"） | `suite.py`、`cli.py`、`samples/4k/Disclaimer.md` |

### 8.3 这些修复会改变分数（必须重跑）

| 机制 | 影响方向 |
|---|---|
| NaN/空集不再算满分 | 原本靠 NaN/崩溃"占便宜"的运行会**下降**；正常运行不变 |
| 子档 ASLN/AJ 改用 r\* | 子档分数与名次会变，通常更严格（与全域同尺） |
| 倒挂短序列不再被稀释 | 锚点少的算法罚款**上升**（2 锚点全倒挂从 0 罚变为满罚） |
| `min_cohort_size=3` 生效 | 51 个单/双样本档位不再做锚点（856 张中 63 张），校准曲线更稳但更粗 |
| 先 round 再乘 | 总分与已发布维度**自洽**，逐条复算不再差 ±0.01 |
| 家族权重重归一（仅说明，未改行为） | 过滤模式下 ACC 仍按在场家族归一，已在 docstring 注明，**跨 filter 分数不可直接比较** |

> 结论：11 个算法必须**全部重跑 standard** 才能得到与 README 一致的新榜单；子档表与 ISACC 需重新生成。

### 8.4 有意未改 / 仍需决策

| 项 | 原因 |
|---|---|
| P0-09 默认 dry-run | `results_*` 是有意 gitignore 的本地产物（用户确认），改成默认 dry-run 会打断现有流程；改为**原子写 + `.bak`**，风险等价可控 |
| P0-10 submodule 改 HTTPS / CI 拉 submodule | `selfcheck` 是**私有**测试仓库，SSH 是正确选择；CI 无凭据也无法拉取，改动无收益 |
| P1-47 LICENSE | 需要主人选定许可证（MIT / Apache-2.0 / 其他），已移除多余 pandas 依赖 |
| P2-10 rate 缺口插值 | 需要改指标层对"缺失 rate 点"的语义，属设计变更，建议单独评估 |
| P2-15 报告产物原子写 | CSV/JSON/MD 仍是直接写；`predictions.csv` 已带 BOM，半截文件风险留给下一轮 |
| P2-21 `sp_sources/` 缺失 | 仍为警告；改为硬失败会打断 sunny-rs 的现有流程，建议主人确认后执行 |
| P2-22 重算审计历史 | 已用 `.bak` 兜底，未实现 `_history` 字段 |
| P2-26 tests/conftest.py | 非仓库根运行仍会 skip 数据集用例 |
| P2-27/28 `assert` 守卫 | `split_reform_intros.py` / `annotate_bench_csv_paths.py` 仍用 `assert`（`python -O` 下失效） |
| P2-29 榜单缺失维度渲染 `0.000` | 未改，建议下轮与 `fmt()` 的 `—` 分支统一 |
| P2-30 JSON 尾换行 | 各工具仍未统一 |

### 8.5 新增回归测试（`tests/test_review_regressions.py`）

覆盖：空集不评分、NaN/inf 不得满分、SP 边界与 overhead 灰区、适配器失败/空输出/多行/花括号模板、worker 暴毙不挂死、圆形 hitSample 往返、全库抽样往返一致、抖动方向与负时间、参数校验、CSV BOM、JSON 拒绝 NaN、package-data、manifest 未解析行报错。

### 8.6 后续口径回退（同日 18:30）

| 项 | 变更 | 原因 |
|---|---|---|
| P1-31 和弦同步抖动 | **已回退为逐音独立抖动** | 同步版本把 AJ 整体抬高（MinaCalc-0.75 52.90 → 80.91），95% 的涨幅来自方向乘子（hardening 0.50 → 0.92）。主人决定保留"逐指独立"的原始定义：玩家的时间误差是按手指发生的。`apply_jitter` 的 docstring 与 `tests/test_review_regressions.py` 已同步改写，17 个算法按旧口径重跑完毕。 |
| 探针丢弃留痕 | 新增 `probe_coverage` / `dropped_probe_maps` / `probe_drops.csv` | 原 `if diff is not None` 静默丢样本，Dan-Overlay 因此丢了 7 张探针谱而无人发现。 |
| `[Colours]` 段落顺序 | `serialize()` 把未知段落移到 `[HitObjects]` **之前** | 原实现写在之后，使 Dan-Overlay 的解析器报 `could not convert string to float: '[Colours]'`，连带 6 条 rate curve 失败（AIN 只有 294/300）。 |

### 8.7 P2-10 落地：r\* 的两端越界改成就地补测（同日 19:00）

| 项 | 变更 | 依据 |
|---|---|---|
| rate 网格 | **1.0 以下不再细化**（只保留 0.9 一个粗锚点） | AIN/CT 都以 `min_rate=1.0` 过滤，0.92~0.98 只服务 r\*，而 17 个算法里有 11 个的读数从不落到 D(0.9) 以下 |
| r\* 越界 | 低端 0.8→0.7、高端 3.0→4.0 **逐级补测**后重新插值；仍越界则夹到阶梯端点 | 原来 `_invert_rate_curve` 返回 None → 该谱静默退出中位数（0.70 覆盖 66.3%、Companella 85.3%） |
| 惩罚地板 | 下降侧地板随跌幅收缩 `f = 10·exp((r*-1)/0.15)`；上升侧维持 10 | 平台地板让 r\*=0.7（难度反转 30%）与 r\*=0.9 只差 1 分 |

实测（MinaCalc-0.75）：覆盖率 0.710 → **1.000**，r\* 1.0218 → 1.0011，AJ 52.90 → **61.79**，速率评估点 12,140 → **11,420**（−5.9%）。

子档位（`_tier_rstar`）**尚未**接入补测：它没有 pool 句柄，仍是"越界即丢"。已知遗留。

