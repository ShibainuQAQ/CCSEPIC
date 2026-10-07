/* M1 固件 —— 启动脚印（诊断用）
 *
 * 为什么需要它：串口是"有输出才算活着"。一旦没输出，就分不清是
 *   ① 根本没跳过来执行（Go 没生效 / 向量表不对）
 *   ② 卡在串口初始化
 *   ③ 卡在 C 运行时（__main）
 *   ④ 卡在主循环
 * 而 **SRAM 的内容在系统复位后不会丢**（只要不断电）。所以可以：
 *
 *     让它跑一会儿 → 复位回 bootloader → 用 Read Memory 读 **0x20004800**
 *
 * 读到的数值直接告诉我们它走到了哪一步 —— 把"静默"变成一串可判定的事实。
 *
 * ⚠️⚠️ 2026-10-04 血泪教训（**换芯片型号时一定要复查这个地址** ✗✗）：
 *    原来写死 **0x2000B000** —— 那是按 **RCT6（48KB RAM，0x20000000~0x2000C000）** 选的 ✓。
 *    换成 **C8T6（20KB RAM，0x20000000~0x20005000）** 之后，0x2000B000 **根本不存在** ✗，
 *    于是 `BOOT_MARKER = ...` 这一句**一写就 HardFault** ✗✗。
 *    症状极具迷惑性：**Reset_Handler 最前面的代码能跑（能看到心跳灯的 3 次快闪 ✓）**，
 *    但从这一句往后**全部停摆** —— 没有开机日志 ✗、没有心跳 ✗、两条串口全哑 ✗，
 *    看起来像"芯片没运行"，其实是"运行到第二句就炸了" ✗。
 *    ⇒ 新地址取 **0x20004800**：在 RW/ZI（约 0x20000000~0x20000800）之上、
 *      也留足了**栈空间**（栈顶 0x20005000 向下长，本工程实际栈深 ≪ 2KB ✓），
 *      两边都不碰 ✓。换回大 RAM 的板子时这个地址**同样安全** ✓（不用改回去）。
 */

#ifndef BOOT_MARKER_H
#define BOOT_MARKER_H

#include <stdint.h>

#define BOOT_MARKER (*(volatile uint32_t *)0x20004800u)

/* 正常路径（按执行顺序递增） */
#define MARK_RESET_ENTERED 0xB0070001u
#define MARK_UART_READY 0xB0070002u
#define MARK_PUTS_DONE 0xB0070003u
#define MARK_RUNTIME_READY 0xB0070004u
#define MARK_MAIN_ENTERED 0xB0070010u
#define MARK_BOARD_INITED 0xB0070011u
#define MARK_BANNER_DONE 0xB0070012u

/* 降级 / 故障 */
#define MARK_PLL_FAILED 0xB00700F1u
#define MARK_FAULT_HARD 0xB0070F01u
#define MARK_FAULT_NMI 0xB0070F02u
#define MARK_FAULT_MEM 0xB0070F03u
#define MARK_FAULT_BUS 0xB0070F04u
#define MARK_FAULT_USAGE 0xB0070F05u

/* 波特率自检块的细分脚印（排查"卡在哪一句"用） */
#define MARK_PROBE_ENTER 0xB0070020u
#define MARK_PROBE_STR 0xB0073100u  /* + i：进入第 i 次迭代 */
#define MARK_PROBE_NUM 0xB0073200u  /* + i：put_u32 完成 */
#define MARK_PROBE_SENT 0xB0073300u /* + i：uart_puts 完成 */
#define MARK_PROBE_DONE 0xB0070021u

#endif /* BOOT_MARKER_H */
