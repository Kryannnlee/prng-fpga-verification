# PRNG-371 FPGA 全流程操作指南

**日期**: 2026-06-18 | **板卡**: ACZ702 (XC7Z020) | **工具**: Vivado 2024.1

---

## 总览

```
Vivado 项目 → ILA IP 配置检查 → 综合 → 实现 → 编程 → ILA 捕获 CSV → Python 比对 → PASS/FAIL
```

> **重要**：Vivado 关闭重开后可能**静默修改 ILA 配置**（将 PROBE 从 DATA AND TRIGGER 降级为 DATA only）。每次重开项目后务必检查步骤 1.1。

---

## 步骤 0：前置条件

- ACZ702 板卡上电，JTAG 连接 PC
- Vivado 2024.1 已安装，hw_server 运行中
- Python 3.7+ 可用

---

## 步骤 1：打开 Vivado 项目

```tcl
# Vivado Tcl Console
open_project D:/fpgaoscillate/prng_project/hw/fpga/vivado_project/prng371_acz702/prng371_acz702.xpr
```

常见警告：

```
WARNING: [IP_Flow 19-3833] IP 'ila_0' is locked
```

**这不是许可证问题。** ILA 是 Xilinx 免费 IP。"locked" 仅仅表示 `.gen/` 缓存的 IP 产物元数据过期（跨机器迁移或 Vivado 重开导致 cache-ID 不匹配）。实现和比特流生成不受影响。

### 步骤 1.1：检查 ILA 探针类型（每次重开项目必查）

**Vivado 重开项目后，ILA IP 配置可能已被静默覆盖。** 原因如下：

Vivado ILA v6.2 的 `C_ADV_TRIGGER = FALSE`（基本触发模式）下，IP 生成器会把 1-bit 探针自动降级为 DATA only——你的 `C_PROBE1_TYPE=1` 配置在 `reset_run` 触发的 IP 重新生成中被**静默改回 0**。结果所有 6 个 probe 全变 DATA only，ILA 不支持硬件触发，`capture_ila.tcl` 只能 fallback 到 free-run（抓到的全是状态 0 的空数据）。

**验证方法**—在 Vivado Tcl Console 中查看：

```tcl
# 查看 ila_0 IP 的实际解析参数
set xci_path [glob D:/fpgaoscillate/prng_project/hw/fpga/vivado_project/prng371_acz702/prng371_acz702.gen/sources_1/ip/ila_0/ila_0.xml]
puts "ILA XML: $xci_path"
```

或者在 `ila_0.xci` 中搜索 `C_PROBE1_TYPE`，看**解析区**的值：

```
# 正确（要使能触发）:  C_PROBE1_TYPE = 1
# 错误（被 Vivado 降级）: C_PROBE1_TYPE = 0
```

**修复方法**：

1. 在 Vivado **IP Sources** 窗格找到 `ila_0`
2. **双击** 打开 Re-customize IP 向导
3. 切到 **Probe_Ports** 页（第二个 tab）
4. 把 **PROBE1** (`dbg_heartbeat`) 从 DATA 改成 **DATA AND TRIGGER**
5. 如果下拉选项灰掉/无法选择：切回 **General** 页 → 把 **Trigger Ports** 从 1 改成 2（或更大）→ 回到 Probe_Ports 页改
6. 点 **OK** → 右键 `ila_0` → **Generate Output Products** → Generate
7. 重新运行步骤 2

> **关键**：GUI Generate 会写正确的缓存。之后只要不执行 `reset_run ila_0_synth_1`，配置就不会再丢。改源码时只 `reset_run synth_1`，不动 ILA 的缓存。

---

## 步骤 2：综合 + 实现 + 比特流

```tcl
# 首次或 ILA 重配后
reset_run synth_1
reset_run impl_1
launch_runs synth_1 -jobs 16
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 16
wait_on_run impl_1
```

**预计时间**：综合 ~1 分钟，实现 ~5-15 分钟。

比特流路径：
```
prng371_acz702.runs/impl_1/acz702_top.bit
```

---

## 步骤 3：ILA 捕获

**确保 ACZ702 板卡已上电且 JTAG 已连接**，在 Vivado Tcl Console 执行：

```tcl
source D:/fpgaoscillate/prng_project/hw/fpga/capture_ila.tcl
```

脚本自动执行：

| 阶段 | 操作 | 预计耗时 |
|------|------|----------|
| 1/5 | 连接 hw_server | ~2s |
| 2/5 | 编程 FPGA | ~5s |
| 3/5 | 刷新设备，检测 ILA | ~10s |
| 4/5 | 配置触发条件（`dbg_heartbeat == 1`） | ~2s |
| 5/5 | 等待触发（~15s 启动门控），上传数据，导出 CSV | ~20-35s |

**触发机制**：PRNG wrapper 有 15 秒启动门控（`STARTUP_DELAY_CYCLES = 750M` @ 50MHz），FSM 在状态 0 等待期间 `dbg_heartbeat = 0`。15 秒后 FSM 离开状态 0，`dbg_heartbeat` 变为 1，ILA 触发，捕获完整的 AXI 写入序列 + PRNG 输出（状态 1→24 为 AXI 写入，状态 25+ 为 PRNG 运行）。

**诊断**：

| 现象 | 原因 | 解决 |
|------|------|------|
| `WARNING: Could not configure heartbeat trigger` | probe1 仍是 DATA only | 回到步骤 1.1 |
| ILA 30s 内不触发 | FSM 卡死或时钟异常 | `source dump_snapshot.tcl` |
| CSV 全 `dbg_state=00` | 启动门控内抓的（free-run 模式） | 回到步骤 1.1 |

成功后 CSV 路径类似：
```
C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado/ila_capture_20260618_121744.csv
```

---

## 步骤 4：CSV 比对验证

打开终端（bash/PowerShell）：

```bash
cd D:/fpgaoscillate/prng_project/prng_experiment/tools

python validate_fpga_prng.py \
  --capture "C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado/ila_capture_YYYYMMDD_HHMMSS.csv" \
  --generate-golden --num-words 8000 --warmup 0 \
  --align-mode cfg_done \
  --report validation_report.md \
  --json-report validation_results.json
```
C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado/
python validate_fpga_prng.py \
  --capture "C:/Users/Kryan/AppData/Roaming/Xilinx/Vivado/ila_capture_20260618_124811.csv" \
  --generate-golden --num-words 8000 --warmup 0 \
  --align-mode cfg_done \
  --report validation_report.md \
  --json-report validation_results.json
**参数说明**：

| 参数 | 说明 |
|------|------|
| `--capture` | ILA 导出的 CSV 文件路径 |
| `--generate-golden` | 自动生成 golden 参考（Python 模型，无需编译 C 库） |
| `--num-words 8000` | 生成 8000 个参考字（CSV 约含 8180 个 PRNG 字） |
| `--warmup 0` | 比对时不跳过任何字（FSM 从冷启动开始） |
| `--align-mode cfg_done` | 用 cfg_done 0→1 跳变对齐（默认值，适用于触发捕获） |
| `--align-mode first_word` | 滑动窗口匹配对齐（用于 free-run 捕获，不依赖 cfg_done） |
| `--golden <file>` | 使用预计算 golden 文件替代 `--generate-golden` |

> **管道填充自动跳过**：`cfg_done` 跳变后，FSM 进入状态 25 的第一个采样点是 PRNG 管道填充周期（值为 0）。验证脚本自动跳过这段空值，无需手动指定 `--skip-capture`。如果对结果有疑问，先跑 `python inspect_axi_writes.py --capture <CSV>` 检查 AXI 写入数据。

---

## 步骤 5：判断结果

### ✅ PASS

```
mismatched words = 0
bit errors = 0
BER = 0
PASS
```

### ❌ FAIL

出现任何 mismatch 时，首先检查：

| 可能原因 | 诊断方法 |
|----------|----------|
| AXI 写入错误（种子未正确写入） | `python inspect_axi_writes.py --capture <CSV>` |
| 预热值不匹配 | 尝试 `--warmup 0` 或 `--warmup 2000` |
| WSTRB bug（旧版 prng_wrapper.v） | 检查 `axi_wstrb` 是否为 `wire [3:0]` + `assign = 4'hF` |
| 比特流过期（源码已修改但未重新综合） | 重新运行步骤 2 |
| ILA 探针类型被降级（捕获的是空数据） | 检查步骤 1.1 |

---

## 常见问题速查

### Q: 为什么 Vivado 重开后 IP 被锁/配置变了？

```
流程：
  关 Vivado → 重开 → .gen 缓存过期 → locked 警告
  → reset_run 触发 IP 重生成
  → Vivado 解析 .xci，把 PROBE1 TYPE 从 1 改成 0
  → ILA 全变 DATA only，触发废了
```

**预防**：重开后先检查 ILA（步骤 1.1）。GUI Generate 后只要不 `reset_run ila_0_synth_1`，缓存不会丢。

### Q: 为什么有时能跑有时不能？

第一次能跑是因为 IP 生成时你在 GUI 里配过，生成的缓存 DCP 是正确的。后续重开+reset_run 触发了重新解析，Vivado 把你的配置覆盖了。这不是你操作的问题，是 Vivado IP generator 的设计行为。

### Q: 怎么快速判断是触发问题还是门控问题？

看 CSV 里 `dbg_state[4:0]` 列：
- 全是 `00` → 门控期内抓的（触发没工作）
- 有 `01`...`19` 等不同值 → 触发工作了，FSM 在跑

---

## 快速参考：常用命令

```tcl
# === Vivado Tcl Console ===

# 查看运行状态
get_runs impl_1

# 查看 IP 状态
report_ip_status

# 只重新生成比特流（源码未改时）
launch_runs impl_1 -to_step write_bitstream -jobs 16

# 完全重做（不改 ILA 时，不动 IP 缓存）
reset_run synth_1
reset_run impl_1
launch_runs synth_1 -jobs 16
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 16
wait_on_run impl_1
```

```bash
# === 终端 ===

# 检查 AXI 写入是否正确（捕获后第一步）
cd D:/fpgaoscillate/prng_project/prng_experiment/tools
python inspect_axi_writes.py --capture <CSV_path>

# 比对新捕获的 CSV
python validate_fpga_prng.py \
  --capture <CSV_path> \
  --generate-golden --num-words 8000 --warmup 0 \
  --report validation_report.md

# 运行工程测试套件
python tests/run_all.py --quick
```

---

## 完整流程检查清单

- [ ] ACZ702 上电，JTAG 连接
- [ ] Vivado 项目打开
- [ ] **ILA PROBE1 = DATA AND TRIGGER（步骤 1.1）**
- [ ] `synth_1` 完成（0 errors）
- [ ] `impl_1` 完成，`acz702_top.bit` 已生成
- [ ] `capture_ila.tcl` 执行成功（**走 TRIGGER 路径，不是 FREE-RUN**），CSV 已导出
- [ ] CSV 中 `dbg_state` 有变化（不是全 `00`）
- [ ] `inspect_axi_writes.py` 确认 AXI 写入正确
- [ ] `validate_fpga_prng.py` 输出 `PASS`（mismatch=0, BER=0）
- [ ] 保存 `validation_report.md` 和 `validation_results.json`

---

## 相关文档

| 文档 | 路径 |
|------|------|
| 设计概要 | [DESIGN_SUMMARY.md](../docs/DESIGN_SUMMARY.md) |
| FPGA 实现指南 | [FPGA_IMPLEMENTATION_GUIDE.md](../docs/FPGA_IMPLEMENTATION_GUIDE.md) |
| 物理验证报告 | [FPGA_PHYSICAL_VALIDATION_REPORT.md](../docs/FPGA_PHYSICAL_VALIDATION_REPORT.md) |
| 工程状态 | [ENGINEERING_STATUS.md](ENGINEERING_STATUS.md) |
| 捕获脚本 | [capture_ila.tcl](capture_ila.tcl) |
| 验证脚本 | `prng_experiment/tools/validate_fpga_prng.py` |
