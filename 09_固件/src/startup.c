/* M1 固件 —— 启动与中断向量表
 *
 * 设计取舍：**不使用 DFP 自带的 armasm 启动文件**，全部用 C 写。
 * 这样工具链只需要 armclang 一个编译器，不必处理 armasm 的条件汇编语法差异，
 * 而且每一行都能被 C 编译器检查。
 *
 * 启动流程：硬件从向量表取 SP 和 Reset_Handler → 本文件的 Reset_Handler → 调 __main
 *          → 由 armlink 的 C 库完成 scatter 加载（把 RW 从 flash 拷到 RAM、把 ZI 清零）
 *          → 进入 main()。
 *
 * ⚠️ 时钟切换（HSI 8MHz → HSE+PLL 72MHz）**不在**这里做，而在 main() 里：
 *    scatter 加载很快、不依赖主频，先让 C 运行时把环境准备好更稳。
 */

#include "boot_marker.h"
#include "stm32f10x.h"

/* ⚠️ 2026-10-04：板子从 **RCT6（48KB SRAM）换成 C8T6（20KB SRAM）**
 *    → 栈顶从 0x2000C000 改成 **0x20005000**（0x20000000 + 20KB）。
 *    必须和 m1.scf 里的 RW_IRAM1 配套：RW/ZI 14KB（到 0x20003800）+ 栈 6KB。 */
#define STACK_TOP 0x20005000u /* C8T6：RAM 顶端 = 0x20000000 + 20KB */

extern int main(void);

/* armlink 按 m1.scf 里的区域名自动生成的符号（`$` 是 ARM 工具链的合法标识符扩展，
 * armclang 实测可编译）。用它们自己完成 RW 拷贝 / ZI 清零，替代 __main 的 scatter 加载。 */
extern uint32_t Load$$RW_IRAM1$$Base[];
extern uint32_t Image$$RW_IRAM1$$Base[];
extern uint32_t Image$$RW_IRAM1$$ZI$$Base[];
extern uint32_t Image$$RW_IRAM1$$ZI$$Limit[];

/* --------------------------------------------------------------------------
 * 中断处理函数：这里只给**弱定义**（死循环/空），需要真正实现的中断
 * （USART1、SysTick）在 board.c 里强定义覆盖。
 * 注意：中断名必须与向量表里的名字一致，链接器才会用强定义替换弱定义。
 * ------------------------------------------------------------------------ */
extern void board_early_uart(void);
extern void board_early_puts(const char *text);

/* 启动脚印（BOOT_MARKER / MARK_*）的说明见 boot_marker.h —— 它是排查
 * "板子毫无反应"这类问题的主力手段。 */

/* 故障也让它开口 —— 一个"死得悄无声息"的单片机是最难查的。
 * 这几句只依赖已经配好的 USART 寄存器，不依赖任何全局变量，因此在早期也安全。
 *
 * 2026-10-03 升级：光印一行 "HARDFAULT" 定位不了任何问题（K210 一收数据就跑飞，
 * 却不知道跑飞到哪）。现在把 **CFSR/HFSR/BFAR/MMFAR + 出错时的 PC/LR** 一起印出来：
 *   · CFSR 的哪一位 = 什么错（IBUSERR/PRECISERR/IMPRECISERR/UNDEFINSTR/UNALIGNED…）
 *   · BFAR/MMFAR 有效时 = 访问的坏地址
 *   · PC = 出错那条指令的地址（拿它去 build\m1.axf 用 fromelf -c 反汇编就能对到函数）
 * 寄存器用**绝对地址**访问，不依赖 CMSIS 头，免得在故障路径里再引入依赖。 */
static void fault_hex(const char *tag, uint32_t v)
{
    static const char hexd[] = "0123456789abcdef";
    char buf[11];
    int i;

    for (i = 0; i < 8; i++) {
        buf[7 - i] = hexd[(v >> (i * 4)) & 0xFu];
    }
    buf[8] = '\0';
    board_early_puts(tag);
    board_early_puts(buf);
}

__attribute__((weak)) void HardFault_Handler(void)
{
    volatile uint32_t *cfsr  = (volatile uint32_t *)0xE000ED28u; /* CFSR  */
    volatile uint32_t *hfsr  = (volatile uint32_t *)0xE000ED2Cu; /* HFSR  */
    volatile uint32_t *mmfar = (volatile uint32_t *)0xE000ED34u; /* MMFAR */
    volatile uint32_t *bfar  = (volatile uint32_t *)0xE000ED38u; /* BFAR  */
    uint32_t exc_return;
    uint32_t *frame;
    uint32_t msp;

    BOOT_MARKER = MARK_FAULT_HARD;
    board_early_puts("LOG fault=HARDFAULT\r\n");

    fault_hex("LOG fault cfsr=0x", *cfsr);
    fault_hex(" hfsr=0x", *hfsr);
    board_early_puts("\r\n");
    fault_hex("LOG fault mmfar=0x", *mmfar);
    fault_hex(" bfar=0x", *bfar);
    board_early_puts("\r\n");

    /* 异常返回码的 bit2：0 = 用的是 MSP，1 = PSP。本项目没有 RTOS，正常都是 MSP。
     * 栈帧里 [6] = PC（出错指令）、[5] = LR、[7] = xPSR。 */
    __asm volatile ("mov %0, lr" : "=r" (exc_return));
    __asm volatile ("mrs %0, msp" : "=r" (msp));
    if ((exc_return & 0x4u) == 0u) {
        frame = (uint32_t *)msp;
        fault_hex("LOG fault pc=0x", frame[6]);
        fault_hex(" lr=0x", frame[5]);
        fault_hex(" psr=0x", frame[7]);
        board_early_puts("\r\n");
    }

    for (;;) { } /* 停在这里，方便接调试器看现场 */
}
__attribute__((weak)) void NMI_Handler(void)
{
    BOOT_MARKER = MARK_FAULT_NMI;
    board_early_puts("LOG fault=NMI\r\n");
    for (;;) { }
}
__attribute__((weak)) void MemManage_Handler(void) { BOOT_MARKER = MARK_FAULT_MEM; board_early_puts("LOG fault=MEM\r\n"); for (;;) { } }
__attribute__((weak)) void BusFault_Handler(void) { BOOT_MARKER = MARK_FAULT_BUS; board_early_puts("LOG fault=BUS\r\n"); for (;;) { } }
__attribute__((weak)) void UsageFault_Handler(void) { BOOT_MARKER = MARK_FAULT_USAGE; board_early_puts("LOG fault=USAGE\r\n"); for (;;) { } }
__attribute__((weak)) void SVC_Handler(void) { for (;;) { } }
__attribute__((weak)) void DebugMon_Handler(void) { for (;;) { } }
__attribute__((weak)) void PendSV_Handler(void) { for (;;) { } }
__attribute__((weak)) void SysTick_Handler(void) { }
__attribute__((weak)) void USART1_IRQHandler(void) { }
__attribute__((weak)) void USART2_IRQHandler(void) { }
__attribute__((weak)) void TIM4_IRQHandler(void) { }   /* M3 步进脉冲发生器（IRQn 30） */
__attribute__((weak)) void USART3_IRQHandler(void) { } /* 视觉板 K210（IRQn 39，C8T6 上就是它） */

void Reset_Handler(void);

/* --------------------------------------------------------------------------
 * 向量表。放进名为 "RESET" 的段，由 m1.scf 的 `*(RESET, +First)` 钉在
 * flash 最开头 0x08000000 —— 这是硬件规定的位置，摆错就永远起不来。
 *
 * 偏移 0x00 是**初始 SP 的值**（不是函数指针），从 0x04 起才是函数指针。
 * ------------------------------------------------------------------------ */
__attribute__((section("RESET"), used))
const uint32_t g_vectors[] = {
    STACK_TOP,                          /* 0x00 初始栈顶 */
    (uint32_t)Reset_Handler,            /* 0x04 复位 */
    (uint32_t)NMI_Handler,              /* 0x08 */
    (uint32_t)HardFault_Handler,        /* 0x0C */
    (uint32_t)MemManage_Handler,        /* 0x10 */
    (uint32_t)BusFault_Handler,         /* 0x14 */
    (uint32_t)UsageFault_Handler,       /* 0x18 */
    0, 0, 0, 0,                         /* 0x1C~0x28 保留 */
    (uint32_t)SVC_Handler,              /* 0x2C */
    (uint32_t)DebugMon_Handler,         /* 0x30 */
    0,                                  /* 0x34 保留 */
    (uint32_t)PendSV_Handler,           /* 0x38 */
    (uint32_t)SysTick_Handler,          /* 0x3C */
    /* 外部中断：**数组下标 = 16 + IRQn**（前 16 项是内核异常）。
     * 本项目 M1 只用到 IRQn 37 = USART1。
     *
     * ⚠️ 血泪教训：这里最初写成 `[37] = USART1_IRQHandler`，等于把 USART1 的向量
     *    放到了 IRQn 21（CAN_RX1）的位置，而**真正的 IRQn 37 位置仍然是 0**。
     *    后果：NVIC_EnableIRQ(USART1_IRQn) 一开中断，一旦有事件就跳到地址 0 跑飞，
     *    现象是"程序莫名卡死"，而不会报任何错 —— 因为这个 16 的偏移完全合法，
     *    编译器也不会警告。将来加 TIM4(30)、EXTI(23) 时同样要 +16。 */
    [16 ... 52] = 0,                            /* IRQn 0 ~ 36 */
    [16 + 30] = (uint32_t)TIM4_IRQHandler,      /* IRQn 30 = TIM4（M3 步进脉冲发生器） */
    [16 + 37] = (uint32_t)USART1_IRQHandler,    /* IRQn 37 = USART1（板载 CH340K 调试口） */
    [16 + 38] = (uint32_t)USART2_IRQHandler,    /* IRQn 38 = USART2（无线桥 C6） */
    [16 + 39 ... 16 + 59] = 0,                  /* IRQn 39 ~ 59 */
    /* ⚠️⚠️ 2026-10-03 血的教训（**指定初始化器是"后面覆盖前面"的**）：
     *    最初把 `[16 + 52] = UART4_IRQHandler` 写在上面那行 `[16 + 39 ... 16 + 59] = 0` **之前**，
     *    结果被后面这行**又清成了 0** ⇒ **UART4 的向量 = 0**。
     *    症状：一收到 K210 的数据就 **HardFault**，`CFSR` 的 bit17 = **INVSTATE**
     *    （跳到地址 0 = 一个非 Thumb 地址）。
     *    ⇒ **凡是"区间清零"必须放在所有具体条目之前**；具体条目一律放最后。
     *    （USART1/2 的 37/38 和 TIM4 的 30 都不在那个区间内，所以一直没暴露。） */
    [16 + 39] = (uint32_t)USART3_IRQHandler,    /* IRQn 39 = USART3（视觉板 K210） */
};

void Reset_Handler(void)
{
    /* ======================================================================
     * ⭐⭐ 最终裁判：**在所有初始化之前**先让 PC13 闪 3 下（2026-10-04 加）
     *
     * 为什么放到这么靠前：换 C8T6 之后出现"烧录 Verify OK、BOOT0=0，但三条链路
     * 全哑、板上的灯也不按我们的意志变"的僵局 ✗。此时唯一能分清的是：
     *    ① 芯片到底有没有执行**我们的向量表** ✓
     *    ② 还是卡在后面的某一步（时钟/串口/GPIOC 初始化）✗
     * 哈佛结构下这两句是纯寄存器写（不依赖 .data/.bss、不依赖时钟初始化 ✓），
     * 所以放在这里**必然**能反映"代码到底跑没跑" ✓。
     *
     * 判据：上电后看到 **3 次快闪**（约 1 秒）⇒ 我们的代码在执行 ✓✓
     *       然后保持亮一下 → 由 board_init() 拉高熄灭 → 之后进入 0.5s 心跳 ✓
     *       完全没有任何闪 ⇒ 根本没执行我们的代码 ✗（BOOT 模式/芯片/烧录问题）
     * ====================================================================== */
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    GPIOC->CRH = (GPIOC->CRH & ~0x00F00000u) | 0x00200000u; /* PC13 推挽输出 2MHz */
    {
        int k;
        for (k = 0; k < 3; k++) {
            volatile int d;
            GPIOC->BSRR = (1u << (16u + 13u));  /* PC13 拉低 = 点亮（蓝板低有效）*/
            for (d = 0; d < 300000; d++) { }
            GPIOC->BSRR = (1u << 13u);          /* 拉高 = 熄灭 */
            for (d = 0; d < 300000; d++) { }
        }
    }

    /* 脚印①：Reset_Handler 真的被执行了。这一条能读出来，就证明
     * 向量表、复位入口、跳转（Go）全都是通的 —— 问题只可能在后面的代码。 */
    BOOT_MARKER = MARK_RESET_ENTERED;

    /* 先把串口配起来（纯寄存器操作，不依赖 C 运行时） */
    board_early_uart();
    BOOT_MARKER = MARK_UART_READY;

    /* 立刻报一句。这一行能出来 = 时钟(HSI)、GPIO、USART 全都对。 */
    board_early_puts("LOG boot stage=0 reset+vector ok\r\n");
    BOOT_MARKER = MARK_PUTS_DONE;

    /* ---- C 运行时初始化：**自己做，不调 __main** ----
     *
     * 为什么不用 __main：实测（脚印 0xB0070003）固件会卡在 __main 里面
     * （__scatterload / __user_setup_stackheap / __rt_lib_init 这一串）。
     * 而这三步对我们真正需要的只有两件事：把 RW 从 flash 搬到 RAM、把 ZI 清零。
     * 自己写出来只有 4 行，可控、可读、不依赖库的内部行为。
     *
     * 代价：不再初始化堆/locale/stdio —— 本工程本来就不用它们（刻意避开 printf，
     * 因为一旦用上 stdio，标准库可能把半主机拖进来，没有调试器时会卡死）。
     */
    {
        uint32_t *src = Load$$RW_IRAM1$$Base;
        uint32_t *dst = Image$$RW_IRAM1$$Base;
        while (dst < Image$$RW_IRAM1$$ZI$$Base) {
            *dst++ = *src++;
        }
        while (dst < Image$$RW_IRAM1$$ZI$$Limit) {
            *dst++ = 0u;
        }
    }
    BOOT_MARKER = MARK_RUNTIME_READY;

    main(); /* 不返回 */
    for (;;) { }
}
