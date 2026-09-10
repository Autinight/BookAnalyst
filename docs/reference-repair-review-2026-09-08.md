# 引用 worker 修复复核（2026-09-08）

任务：`6e3f071b078b4479a381209dd4401dbe`。本报告针对 references 阶段的最终补丁；收尾阶段可能继续修改 TeX。页码均为 PDF 物理页。

## 实际结果

11 个重复标签组、51 个缺失引用组已完成。修改 16 个标签与 74 处引用。修复前有 11 个重复键、110 处不能唯一解析的引用；修复后程序扫描两项均为零。110 不等于实际改写次数：部分引用只需目标标签消歧即可唯一解析。

| 修改类别 | 引用处数 |
|---|---:|
| 重复标签消歧后的引用跟随 | 16 |
| 文献键改绑 | 8 |
| 数学结果编号改绑 | 6 |
| 定理/引理/推论类型改绑 | 12 |
| prime 编码统一 | 2 |
| problem/exercise 命名统一 | 30 |

## 如何执行

先按重复键分组，各 worker 根据上下文和 heading 作用域给冲突标签增加后缀，按引用位置 ID 更新相关引用。程序合并这些结果后，再按缺失引用键分组，worker 从已存在目标中选择并提交引用键修改。最终验证只保证修改位置归属正确、标签前两段不被改变、目标唯一且存在；不验证数学语义或文献身份。

保存下来的 48 组查询记录包含 84 轮工具动作、133 次搜索、116 次正文上下文读取；18 组请求过原 PDF 页图，共 48 次页图请求（含重复页）。另外 14 组没有保存的额外查询记录，可直接使用初始提供的候选和上下文。上述统计是当前保留的分组历史，不是所有旧尝试的累计工具数。请求数据库中 references 共 227 条调用，199 条完成、28 条失败，包含历史尝试和请求重试；调用完成不等于该次候选通过校验。

## 代表性修复与复核意见

- `bibliography:FS` 被区分为 `FS:Fabes-Stroock`、`FS:Fefferman-Stein`、`FS:Finn-Serrin`；worker 读取 PDF 第 268、314、330、510 页的转换正文，按引用上下文绑定对应作者组合。
- `problem:*` 改成已有的 `exercise:*`，属于命名不一致修复；例如 `problem:7.1 → exercise:7.1`，查询了习题标签及第 170、186、210 页正文。
- `equation:4.17' → equation:4.17prime`、双撇号对应 `primeprime`；是同一编号的编码差异。
- `corollary:12.2 → corollary:11.2`：第 321 页原书确实写 Corollary 12.2，但所述 Schauder 不动点结论与第 295 页 Corollary 11.2 一致。此改动有正文依据，同时已超出纯键格式修复，属于原书引用疑似错误的修正。
- `lemma:4.3 → lemma:4.2`：第 244 页原书确实写 Lemma 4.3；第 68 页 Lemma 4.2 明确给出 Newtonian potential 满足 Δw=f。改绑具有内容依据。
- `theorem:8.28 → theorem:8.20`：目标第 212 页确为完整 Harnack inequality；仍应将这种编号变更记录为语义修正。
- **待复核：`bibliography:MM → bibliography:MV2`**。第 152 页原书写 `[MM]`；第 515 页 `[MV 2]` 是 Mamedov 的 “Behavior near the boundary of solutions of degenerate second order elliptic equations”。该 worker 查询了 9 轮、查看多页文献，未找到 MM 条目。现存证据只能说明题材接近，没有证明 MM 就是 MV2，应保留为待确认，不能把校验通过视为身份已核实。
- **重复标签并不总是不同对象**：`theorem:11.4` 在同一定理开始和跨页结尾各有一个 label，`theorem:11.5` 在同一定理首尾也重复打标。worker 把后者分别改成 `theorem:11.4:11.2`、`theorem:11.5:chapter-11`，留下两个不同键指向同一对象。消除了键冲突，但没有去除重复打标；当前编辑结构也没有删除 label 的操作。
- **命名后缀未统一为章节坐标**：当前混用 `ch4`、`chapter-11`、`section-9.9`、`11.2`、`problems`、标题文字和作者名。后续若按 heading 坐标重命名，需要独立的规范化步骤。
- **可见正文类型词未同步**：例如修改 `theorem:6.1 → lemma:6.1` 时，引用前面的 “Theorem” 文字仍保留；当前 references 补丁仅改花括号内的键。收尾是否进一步修正需另行核对。

## 之前十轮卡住的原因

截图中的 `reference-duplicates-5793ee954ecea3ff` 对应 `equation:9.70`。历史候选反复生成 `equation:9.70-problems` 或 `equation:9.70-section-9.9`，程序按冒号拆分，认为第二段原书编号从 `9.70` 被改成 `9.70-problems`，因此抛出 `SYMBOL_EDIT`。最终使用 `equation:9.70:section-9.9` 与 `equation:9.70:problems` 通过。

worker 曾花查询轮次搜索全书是否存在 `-section-`、`-problems` 等命名模式，说明提示未明确限定冒号后缀格式、反馈未给出合法示例，导致它反复猜格式。这一部分不能仅归因于书太长或并发不足。

## 全部标签修改

| PDF 页 | 位置 ID | 原键 | 新键 |
|---:|---|---|---|
| 523 | `p523-label-5` | `bibliography:CL` | `bibliography:CL:epilogue` |
| 510 | `p510-label-3` | `bibliography:FS` | `bibliography:FS:Fabes-Stroock` |
| 510 | `p510-label-5` | `bibliography:FS` | `bibliography:FS:Fefferman-Stein` |
| 510 | `p510-label-12` | `bibliography:FS` | `bibliography:FS:Finn-Serrin` |
| 511 | `p511-label-19` | `bibliography:HL` | `bibliography:HL:ch7` |
| 512 | `p512-label-3` | `bibliography:HL` | `bibliography:HL:ch2` |
| 496 | `p496-label-1` | `equation:17.86` | `equation:17.86:section-17.8-alternative-approach` |
| 66 | `p66-label-2` | `equation:4.6` | `equation:4.6:ch4` |
| 268 | `p268-label-3` | `equation:9.68` | `equation:9.68:chapter-9-problems` |
| 266 | `p266-label-2` | `equation:9.69` | `equation:9.69:boundary-holder-estimates-for-the-gradient` |
| 269 | `p269-label-2` | `equation:9.69` | `equation:9.69:problems` |
| 267 | `p267-label-0` | `equation:9.70` | `equation:9.70:section-9.9` |
| 269 | `p269-label-10` | `equation:9.70` | `equation:9.70:problems` |
| 297 | `p297-label-0` | `theorem:11.4` | `theorem:11.4:11.2` |
| 298 | `p298-label-1` | `theorem:11.5` | `theorem:11.5:chapter-11` |
| 269 | `p269-label-1` | `theorem:9.31` | `theorem:9.31:problems` |

## 全部引用修改

| PDF 页 | 位置 ID | 原键 | 新键 | 类别 |
|---:|---|---|---|---|
| 522 | `p522-reference-1` | `bibliography:CL` | `bibliography:CL:epilogue` | 重复标签消歧后的引用跟随 |
| 268 | `p268-reference-6` | `bibliography:FS` | `bibliography:FS:Fefferman-Stein` | 重复标签消歧后的引用跟随 |
| 314 | `p314-reference-1` | `bibliography:FS` | `bibliography:FS:Finn-Serrin` | 重复标签消歧后的引用跟随 |
| 330 | `p330-reference-2` | `bibliography:FS` | `bibliography:FS:Finn-Serrin` | 重复标签消歧后的引用跟随 |
| 330 | `p330-reference-3` | `bibliography:FS` | `bibliography:FS:Finn-Serrin` | 重复标签消歧后的引用跟随 |
| 38 | `p38-reference-4` | `bibliography:HL` | `bibliography:HL:ch2` | 重复标签消歧后的引用跟随 |
| 173 | `p173-reference-2` | `bibliography:HL` | `bibliography:HL:ch7` | 重复标签消歧后的引用跟随 |
| 497 | `p497-reference-7` | `equation:17.86` | `equation:17.86:section-17.8-alternative-approach` | 重复标签消歧后的引用跟随 |
| 86 | `p86-reference-3` | `equation:4.6` | `equation:4.6:ch4` | 重复标签消歧后的引用跟随 |
| 99 | `p99-reference-1` | `equation:4.6` | `equation:4.6:ch4` | 重复标签消歧后的引用跟随 |
| 136 | `p136-reference-3` | `equation:4.6` | `equation:4.6:ch4` | 重复标签消歧后的引用跟随 |
| 268 | `p268-reference-50` | `equation:9.68` | `equation:9.68:chapter-9-problems` | 重复标签消歧后的引用跟随 |
| 266 | `p266-reference-2` | `equation:9.69` | `equation:9.69:boundary-holder-estimates-for-the-gradient` | 重复标签消歧后的引用跟随 |
| 266 | `p266-reference-4` | `equation:9.69` | `equation:9.69:boundary-holder-estimates-for-the-gradient` | 重复标签消歧后的引用跟随 |
| 267 | `p267-reference-1` | `equation:9.69` | `equation:9.69:boundary-holder-estimates-for-the-gradient` | 重复标签消歧后的引用跟随 |
| 267 | `p267-reference-2` | `equation:9.70` | `equation:9.70:section-9.9` | 重复标签消歧后的引用跟随 |
| 452 | `p452-reference-39` | `bibliography:GE` | `bibliography:GE2` | 文献键改绑 |
| 452 | `p452-reference-35` | `bibliography:GI` | `bibliography:GI1` | 文献键改绑 |
| 452 | `p452-reference-40` | `bibliography:GI` | `bibliography:GI2` | 文献键改绑 |
| 503 | `p503-reference-27` | `bibliography:LBT` | `bibliography:LT` | 文献键改绑 |
| 152 | `p152-reference-6` | `bibliography:MM` | `bibliography:MV2` | 文献键改绑 |
| 268 | `p268-reference-49` | `bibliography:TA5` | `bibliography:TA4` | 文献键改绑 |
| 151 | `p151-reference-15` | `bibliography:WI1` | `bibliography:Wl1` | 文献键改绑 |
| 228 | `p228-reference-39` | `bibliography:WI3` | `bibliography:Wl3` | 文献键改绑 |
| 321 | `p321-reference-7` | `corollary:12.2` | `corollary:11.2` | 数学结果编号改绑 |
| 255 | `p255-reference-11` | `corollary:9.14` | `theorem:9.14` | 定理/引理/推论类型改绑 |
| 256 | `p256-reference-7` | `corollary:9.14` | `theorem:9.14` | 定理/引理/推论类型改绑 |
| 269 | `p269-reference-7` | `corollary:9.14` | `theorem:9.14` | 定理/引理/推论类型改绑 |
| 148 | `p148-reference-12` | `equation:4.17'` | `equation:4.17prime` | prime 编码统一 |
| 148 | `p148-reference-13` | `equation:4.17''` | `equation:4.17primeprime` | prime 编码统一 |
| 244 | `p244-reference-0` | `lemma:4.3` | `lemma:4.2` | 数学结果编号改绑 |
| 183 | `p183-reference-1` | `lemma:7.4` | `theorem:7.4` | 定理/引理/推论类型改绑 |
| 449 | `p449-reference-1` | `problem:16.5` | `exercise:16.5` | problem/exercise 命名统一 |
| 458 | `p458-reference-0` | `problem:17.1` | `exercise:17.1` | problem/exercise 命名统一 |
| 480 | `p480-reference-14` | `problem:17.4` | `exercise:17.4` | problem/exercise 命名统一 |
| 486 | `p486-reference-1` | `problem:17.5` | `exercise:17.5` | problem/exercise 命名统一 |
| 486 | `p486-reference-12` | `problem:17.6` | `exercise:17.6` | problem/exercise 命名统一 |
| 486 | `p486-reference-13` | `problem:17.7` | `exercise:17.7` | problem/exercise 命名统一 |
| 488 | `p488-reference-16` | `problem:17.8` | `exercise:17.8` | problem/exercise 命名统一 |
| 29 | `p29-reference-3` | `problem:2.1` | `exercise:2.1` | problem/exercise 命名统一 |
| 134 | `p134-reference-4` | `problem:2.4` | `exercise:2.4` | problem/exercise 命名统一 |
| 142 | `p142-reference-5` | `problem:3.1` | `exercise:3.1` | problem/exercise 命名统一 |
| 86 | `p86-reference-5` | `problem:5.1` | `exercise:5.1` | problem/exercise 命名统一 |
| 86 | `p86-reference-6` | `problem:5.2` | `exercise:5.2` | problem/exercise 命名统一 |
| 133 | `p133-reference-9` | `problem:6.10` | `exercise:6.10` | problem/exercise 命名统一 |
| 119 | `p119-reference-4` | `problem:6.3` | `exercise:6.3` | problem/exercise 命名统一 |
| 265 | `p265-reference-6` | `problem:6.3` | `exercise:6.3` | problem/exercise 命名统一 |
| 131 | `p131-reference-3` | `problem:6.4` | `exercise:6.4` | problem/exercise 命名统一 |
| 167 | `p167-reference-8` | `problem:6.8` | `exercise:6.8` | problem/exercise 命名统一 |
| 170 | `p170-reference-1` | `problem:7.1` | `exercise:7.1` | problem/exercise 命名统一 |
| 210 | `p210-reference-0` | `problem:7.1` | `exercise:7.1` | problem/exercise 命名统一 |
| 166 | `p166-reference-8` | `problem:7.10` | `exercise:7.10` | problem/exercise 命名统一 |
| 168 | `p168-reference-4` | `problem:7.11` | `exercise:7.11` | problem/exercise 命名统一 |
| 164 | `p164-reference-0` | `problem:7.4` | `exercise:7.4` | problem/exercise 命名统一 |
| 164 | `p164-reference-1` | `problem:7.5` | `exercise:7.5` | problem/exercise 命名统一 |
| 167 | `p167-reference-5` | `problem:7.7` | `exercise:7.7` | problem/exercise 命名统一 |
| 164 | `p164-reference-3` | `problem:7.8` | `exercise:7.8` | problem/exercise 命名统一 |
| 166 | `p166-reference-6` | `problem:7.8` | `exercise:7.8` | problem/exercise 命名统一 |
| 198 | `p198-reference-6` | `problem:8.2` | `exercise:8.2` | problem/exercise 命名统一 |
| 212 | `p212-reference-11` | `problem:8.3` | `exercise:8.3` | problem/exercise 命名统一 |
| 54 | `p54-reference-4` | `problem:8.4` | `exercise:8.4` | problem/exercise 命名统一 |
| 215 | `p215-reference-3` | `problem:8.6` | `exercise:8.6` | problem/exercise 命名统一 |
| 421 | `p421-reference-11` | `theorem:12.1` | `theorem:12.3` | 数学结果编号改绑 |
| 373 | `p373-reference-13` | `theorem:14.5` | `corollary:14.5` | 定理/引理/推论类型改绑 |
| 223 | `p223-reference-8` | `theorem:6.1` | `lemma:6.1` | 定理/引理/推论类型改绑 |
| 248 | `p248-reference-10` | `theorem:6.1` | `lemma:6.1` | 定理/引理/推论类型改绑 |
| 500 | `p500-reference-1` | `theorem:6.35` | `lemma:6.35` | 定理/引理/推论类型改绑 |
| 184 | `p184-reference-2` | `theorem:7.12` | `lemma:7.12` | 定理/引理/推论类型改绑 |
| 200 | `p200-reference-12` | `theorem:8.11` | `corollary:8.11` | 定理/引理/推论类型改绑 |
| 229 | `p229-reference-5` | `theorem:8.2` | `theorem:8.3` | 数学结果编号改绑 |
| 227 | `p227-reference-12` | `theorem:8.21` | `corollary:8.21` | 定理/引理/推论类型改绑 |
| 454 | `p454-reference-6` | `theorem:8.28` | `theorem:8.20` | 数学结果编号改绑 |
| 204 | `p204-reference-5` | `theorem:8.5` | `theorem:8.15` | 数学结果编号改绑 |
| 206 | `p206-reference-8` | `theorem:9.7` | `lemma:9.7` | 定理/引理/推论类型改绑 |

## 证据文件

- `.bookanalyst/runs/6e3f071b078b4479a381209dd4401dbe/v7/joined.json`：修复前正文。
- 同目录 `heading-edits.json`、`reference-edits.json`、`symbols.json`：最终标题、引用补丁、引用阶段符号索引。
- 同目录 `reference-groups/*-queries.json`：工具查询与返回证据。
- `.bookanalyst/runs/6e3f071b078b4479a381209dd4401dbe/requests/*/request.json`：历史候选与校验反馈。
- `tests/data/books/elliptic-pde-second-order.pdf`：本轮抽查的原书。

本轮只分析并生成报告，未修改书籍结果或 worker 实现。引用阶段已结束，已暂停该阶段的进度监控。
