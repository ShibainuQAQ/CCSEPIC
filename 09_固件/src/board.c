/* M1 固件 —— 板级支持（时钟 / 滴答 / GPIO / USART1）
 *
 * 全部用 CMSIS 的寄存器定义直接编程，不依赖标准外设库（SPL）：
 * M1 只需要 4 个外设（RCC/GPIO/USART/SysTick），引 SPL 反而增加构建面。
 *
 * 引脚（与《固件设计-协议与引脚》§8 一致）：
 *   PA9  USART1_TX  → 板载 CH340K → PC 的 COM 口
 *   PA10 USART1_RX
 * 串口参数 **115200 8E1**（偶校验，与出厂 bootloader 一致，见 usart_baud 注释）。
 *
 * ⚠️ 本文件最重要的设计原则：**永远不要"悄悄地卡住"**。
 *    HSE 晶振不起振、PLL 不锁定这类故障，如果只写死等循环，表现就是"板子毫无反应"，
 *    排查代价极高。所以这里每一步都带超时，并且**先把串口跑起来再切主频**，
 *    保证任何阶段出问题都能吐出一行可读的诊断。
 */

#include "board.h"

#include "boot_marker.h"
#include "stm32f10x.h"

/* 目标主频。不复用 DFP 的 SystemCoreClock —— 那个符号来自 system_stm32f10x.c，
 * 而本工程刻意不编译它。改 PLL 倍频就必须同时改这里，否则 SysTick 和波特率会一起错。 */
#define CPU_HZ_HSI 8000000u
#define CPU_HZ_PLL 72000000u

/* M1 先**不用 PLL**，就跑在 HSI 8MHz 上。
 *
 * 原因：2026-09-21 实测发现——用 PLL 时 PC 收到的全是乱码，且乱码呈"前 7 位正确、
 * 第 8 位起漂移"的形态，这是波特率偏差约 8% 的典型特征。而"晶振到底是不是 8MHz"
 * 既没有丝印可读、也没有示波器可量。所以 M1 阶段先把时钟源固定成 HSI 这一个，
 * 用 main() 里的"波特率自检"把它测准，再决定 PLL 怎么配。
 *
 * 为什么可以先不追时钟精度：M1 只做串口协议与状态机，8MHz 完全够。
 * 步进脉冲引擎（M3）才真正需要高主频 —— 那时这个数已经测准了。
 */
#define USE_PLL 0

/* M1 阶段：SYSCLK 用**板上晶振（HSE 8MHz）**而不是 HSI。
 *
 * 2026-09-22 实测定位：HSI 这块片子偏了约 5%（PROBE 自检里 BRR=69 与 75 两档都只是
 * "勉强能读"，对应 f_cpu ≈ 115200×73 ≈ 8.4MHz）。而 8E1 只有 1 位停止位，波特率
 * 容差本来就只有 ±2% 左右。5% 偏差的症状极有迷惑性：**帧结构完全正确**（\r\n 原样、
 * 字符位置一一对应、低半字节基本都对），只有每个字节靠后的几位采错 ——
 * 看起来就是"字符大体认识、高位时不时多一个 1"，非常容易被误判成"干扰"或"帧格式错"。
 * 换晶振（±30ppm）后 BRR=69 的误差只有 0.6%，问题从根上消失。
 * 注意 HSE 与 HSI 同为 8MHz，所以切换前后 BRR 不用改。 */
#define USE_HSE 1

#define UART_BAUD 115200u

/* 临时实验开关：强制 BRR 为固定值（0 = 不用，走自校准）。
 * 2026-09-22 实测：**69 和 70 都试过，输出都是乱码** → 排除"单纯波特率偏一个数"的可能，
 * 说明还有别的因素（详见 Claude接续日志.md 的排查记录）。已还原为 0。 */
#define BRR_FORCE 0u

/* 波特率自校准开关：0 = 关（M1 阶段已改用晶振 HSE，不需要再校准）。
 *
 * 为什么要关：这个函数是为了对付"HSI 偏差导致 BRR 处于容差边缘"而写的，
 * 但它一直没真正生效（实测 BRR 仍是 69），而且**每次启动都要死等 ~10 秒**
 * （等主机发 64 个字节来测时长）—— 排查时这 10 秒会严重拖慢每一轮
 * "编译→烧录→抓包"的迭代。改用晶振后它的前提也不成立了。
 * 保留代码与开关，将来若真要支持"无晶振板"再启用。 */
#define AUTOCAL 0

/* M1 用**轮询**收字节，不开接收中断。
 *
 * 为什么：2026-09-21 实测发现——一旦在 board_init 末尾 `__enable_irq()`，
 * main 里第一次 uart_puts 就卡死在 TXE 轮询上（而中断关闭时同一函数完全正常）。
 * M1 的主循环本来就无事可做，轮询在 115200 下不会丢字节（一字节 87µs，
 * 主循环一圈只有几条指令），所以先用轮询把通信与协议跑通。
 *
 * 中断收字节的能力**保留在代码里**（`UART_RX_IRQ 1` 即可切回），
 * 留到 M3 —— 那时步进引擎必须用定时器中断，有明确的时序需求，
 * 才好一并判断优先级、临界区与中断嵌套。先解决"能动"，再解决"快"。 */
/* M1 曾用**轮询**收字节，不开接收中断。2026-09-22 起**默认开接收中断**。
 *
 * 历史：2026-09-21 记录"一旦在 board_init 末尾 `__enable_irq()`，main 里第一次
 * uart_puts 就卡死在 TXE 轮询上"，于是关掉中断绕过去。
 * **2026-09-22 实测订正（线路 A）**：那是**误判** —— 真凶是**向量表下标错位**
 * （USART1 的向量被放在 IRQn 21、真正的 IRQn 37 是 0，见 startup.c 第 89-93 行）：
 * 一开中断 + RX 线上一个噪声起始位 → 跳到地址 0 跑飞，卡死正好落在随后的
 * 第一次 uart_puts 上。该错误已修，实验 1（只剩 SysTick）与实验 2（SysTick +
 * USART1 RX 都开）**双双通过**：banner 完整、`PING → OK pong`。
 * → 现在**开中断**：M3 的步进引擎必须用定时器中断，中断路径必须常态可用；
 *   而且中断收字节比轮询更抗主循环变慢（M3 主循环会忙起来）。
 *   置 0 仍可用于"怀疑中断时快速排除"。 */
#define UART_RX_IRQ 1

/* 死等循环的兜底次数：约几十毫秒量级。宁可报错降频，也不要无声卡死。 */
#define WAIT_GUARD 2000000u

/* --------------------------------------------------------------------------
 * 接收环形缓冲区
 * 中断里只做"塞进缓冲区"这一件事（够快、不丢字节），解析放到主循环。
 * 单生产者（中断）单消费者（主循环）、容量是 2 的幂、无符号回绕比较，
 * 因此 head/tail 各写各的，不需要临界区。
 * ------------------------------------------------------------------------ */
#define RXBUF_SIZE 256u /* 必须是 2 的幂 */
static volatile uint8_t s_rx[RXBUF_SIZE];
static volatile uint32_t s_rx_head;
static volatile uint32_t s_rx_tail;

static volatile uint32_t s_millis;
static uint32_t s_cpu_hz = CPU_HZ_HSI; /* 实际主频；波特率换算依赖它 */

/* --------------------------------------------------------------------------
 * 串口底层
 * ------------------------------------------------------------------------ */
static void uart_apply_baud(uint32_t cpu_hz)
{
    /* STM32 的 BRR 是定点数：BRR = f_cpu / 波特率（四舍五入），
     * 等价于 USARTDIV = f/(16*baud) 后再 <<4。
     * 例：72e6/115200 = 625 整；8e6/115200 = 69.44 → 69（误差 0.6%，可接受）。 */
    uint32_t brr = (cpu_hz + UART_BAUD / 2u) / UART_BAUD;

#if BRR_FORCE
    brr = BRR_FORCE; /* 临时实验：见 BRR_FORCE 定义处的说明 */
#endif

    USART1->CR1 = 0; /* 改配置前先关 UE，避免半个字节的毛刺 */
    USART1->BRR = brr;
    /* 115200 **8E1（偶校验）**：与出厂 ROM bootloader 保持一致 ——
     *   ① 烧录与应用通信只用一种参数，不必切换（切换要重开端口，而重开会经自动
     *      ISP 电路复位板子，极易自乱阵脚）；
     *   ② 偶校验给"电机干扰导致字节翻位"提供一道几乎免费的检测。
     * 注意 F1 的极性：CR1.PS = 0 才是**偶**校验（PS=1 是奇校验），这里不置 PS。 */
    USART1->CR1 = UART_CR1_8E1_RX;
}

static void uart_gpio_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_USART1EN;
    /* CRH 每 4 位管一个引脚，引脚 n（8~15）占 bit[4*(n-8) .. 4*(n-8)+3]：
     *     PA9  → bit[7:4]  = 0xB → 复用推挽输出、50MHz   （USART1_TX）
     *     PA10 → bit[11:8] = 0x4 → 浮空输入              （USART1_RX）
     *   合起来 = (0xB << 4) | (0x4 << 8) = 0x4B0
     *
     * ⚠️ 血泪教训：这里最初误写成 0xB40，**把 TX 和 RX 的高低位搞反了** ——
     *    PA9 变成浮空输入、PA10 变成推挽输出。症状极具迷惑性：USART 内部照常
     *    移位（TXE/TC 正常置位、发送函数正常返回），但**信号根本没到引脚**，
     *    表现就是"固件明明在跑、却收不到任何字节"。查了很久才想起来手算这个半字。
     */
    GPIOA->CRH = (GPIOA->CRH & ~0x00000FF0u) | 0x000004B0u;
}

/* USART2（无线桥）/ UART4（视觉板 K210）的收发实现定义在本文件后半部分（BRR 需要 pclk1_hz()，
 * 而那个函数在串口屏那一节里），所以这里先给个前置声明。 */
void u2_send_text(const char *text);
void u2_send_bytes(const uint8_t *data, uint32_t len);
void u4_send_text(const char *text);
void u4_send_bytes(const uint8_t *data, uint32_t len);

/* 回复发到哪个口：1 = USART1（板载 CH340K），2 = USART2（无线桥 C6），4 = UART4（视觉板 K210）。
 * 主循环在解析一行之前用 uart_set_out_port() 设好，解析完再设回 1 ——
 * 这样**上层上百处 uart_puts 一行都不用改**，回复自然跟着命令来的那个口回去。 */
static int s_out_port = 1;

void uart_set_out_port(int port) { s_out_port = port; }
int  uart_out_port(void) { return s_out_port; }

void uart_puts(const char *text)
{
    if (s_out_port == 2) {
        u2_send_text(text); /* 无线桥：走 USART2 */
        return;
    }
    if (s_out_port == 4) {
        u4_send_text(text); /* 视觉板：走 UART4 */
        return;
    }
    while (*text != '\0') {
        BOOT_MARKER = 0xB0074000u; /* 脚印：准备等 TXE */
        while ((USART1->SR & USART_SR_TXE) == 0) { }
        BOOT_MARKER = 0xB0074001u; /* 脚印：TXE 已置位，准备写 DR */
        USART1->DR = (uint16_t)(uint8_t)(*text++);
    }
    BOOT_MARKER = 0xB0074002u; /* 脚印：全部写完，准备等 TC */
    while ((USART1->SR & USART_SR_TC) == 0) { } /* 等最后一字节真正发完 */
    BOOT_MARKER = 0xB0074003u; /* 脚印：TC 已置位 */
}

/* --------------------------------------------------------------------------
 * 早期串口：**在 C 运行时初始化之前**就能说话
 *
 * 只做寄存器操作、只用局部变量 —— 此时 .data 还没从 flash 拷到 RAM，
 * 一旦碰了已初始化的全局变量就会读到垃圾。
 * 用途：把"板子到底死在哪一步"变成一条看得见的信息（见 startup.c 的调用点）。
 * ------------------------------------------------------------------------ */
void board_early_uart(void)
{
    uart_gpio_init();
    USART1->CR1 = 0;
    USART1->BRR = (CPU_HZ_HSI + UART_BAUD / 2u) / UART_BAUD;
    USART1->CR1 = UART_CR1_8E1;
}

void board_early_puts(const char *text)
{
    while (*text != '\0') {
        while ((USART1->SR & USART_SR_TXE) == 0) { }
        USART1->DR = (uint16_t)(uint8_t)(*text++);
    }
    while ((USART1->SR & USART_SR_TC) == 0) { }
}

/* --------------------------------------------------------------------------
 * SysTick：1ms 一次。M1 只用来计时；M3 的步进引擎会另外用 TIM4。
 * ------------------------------------------------------------------------ */
static void systick_init(void)
{
    SysTick->LOAD = (s_cpu_hz / 1000u) - 1u;
    SysTick->VAL = 0;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_TICKINT_Msk |
                    SysTick_CTRL_ENABLE_Msk;
}

void SysTick_Handler(void)
{
    s_millis++;
}

uint32_t board_millis(void)
{
    return s_millis;
}

/* --------------------------------------------------------------------------
 * 时钟：先拉回一个**已知状态**，再决定要不要上 PLL
 *
 * ⚠️ 为什么要"先拉回"：这是踩出来的坑。
 *   用串口 ISP 烧录时，MCU 是**从 ROM bootloader 直接跳进应用的**，而 bootloader
 *   已经在 RCC_CFGR 里设过东西（实测它把 APB2 分频设成了 /2，证据是它的
 *   USART1->BRR = 34 —— 115200 在 8MHz 下应该是 69）。
 *   应用如果**不显式配置时钟**，就会继承这个 /2 → PCLK2 只有 4MHz，
 *   而 BRR 是按 8MHz 算的 → **实际波特率只有标称的一半 → 串口全是乱码**。
 *   更阴的是：冷启动（BOOT0=0 复位）时 RCC_CFGR 是复位值，那时反而是好的
 *   —— 于是表现为"bootloader 烧完不正常、重上电却正常"，极难查。
 *
 * 结论：**启动时永远显式配置时钟，不继承任何前序状态**。这一条对量产固件同样成立。
 * ------------------------------------------------------------------------ */
static void clock_reset_to_hsi(void)
{
    uint32_t guard;

    /* 切回 HSI（若原来跑在 PLL 上，先切走再关 PLL，否则会短暂停摆） */
    RCC->CR |= RCC_CR_HSION;
    for (guard = 0; (RCC->CR & RCC_CR_HSIRDY) == 0; guard++) {
        if (guard > WAIT_GUARD) {
            break; /* HSI 是芯片内部振荡器，起不来基本不可能；真起不来也没别的办法 */
        }
    }
    RCC->CFGR &= ~RCC_CFGR_SW; /* SW = 00 → SYSCLK 选 HSI */
    for (guard = 0; (RCC->CFGR & RCC_CFGR_SWS) != 0u; guard++) {
        if (guard > WAIT_GUARD) {
            break;
        }
    }

    /* 关掉 PLL / HSE / CSS，并把所有分频清零（HPRE/PPRE1/PPRE2 全 = /1） */
    RCC->CR &= ~(RCC_CR_PLLON | RCC_CR_HSEON | RCC_CR_CSSON);
    for (guard = 0; (RCC->CR & RCC_CR_PLLRDY) != 0u; guard++) {
        if (guard > WAIT_GUARD) {
            break;
        }
    }
    RCC->CFGR = 0;

    /* 此时：SYSCLK = HSI = 8MHz，HCLK = PCLK1 = PCLK2 = 8MHz */
}

/* --------------------------------------------------------------------------
 * 时钟：切到 HSE（板上 8.000MHz 晶振）
 * 返回 1 = 成功（SYSCLK = 8MHz，来自晶振）；0 = 失败（保持 HSI，调用方负责降级）
 *
 * 详细理由见文件上方 USE_HSE 的注释：HSI 是片内 RC，实测偏 ~5%，
 * 而 8E1 的波特率容差只有 ±2% 左右 → 就是"串口乱码"的真凶。
 * ------------------------------------------------------------------------ */
#define CPU_HZ_HSE 8000000u

static int clock_to_hse(void)
{
    uint32_t guard;

    RCC->CR |= RCC_CR_HSEON;
    for (guard = 0; (RCC->CR & RCC_CR_HSERDY) == 0; guard++) {
        if (guard > WAIT_GUARD) {
            RCC->CR &= ~RCC_CR_HSEON; /* 起振失败就撤销，别让总线悬着 */
            return 0;                 /* 晶振没焊 / 负载电容不对 / 频率不对 */
        }
    }

    /* 分频全 /1，SW = 01 → SYSCLK 直接来自 HSE。同频 8MHz，故 BRR 无需重算。 */
    RCC->CFGR = RCC_CFGR_SW_HSE;
    for (guard = 0; (RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_HSE; guard++) {
        if (guard > WAIT_GUARD) {
            return 0;
        }
    }
    return 1;
}

/* --------------------------------------------------------------------------
 * 时钟：HSI 8MHz → HSE + PLL×9 = 72MHz
 * 返回 1 = 成功切到 72MHz；0 = 失败（调用方负责降级并报出来）
 *
 * ⚠️ 整个函数被 `#if USE_PLL` 包住：M1 阶段 USE_PLL=0，它根本不参与编译。
 * ------------------------------------------------------------------------ */
#if USE_PLL
static int clock_to_pll(void)
{
    uint32_t guard;

    /* 1. 开 HSE 并等它稳定。**必须带超时**：晶振没焊/坏了时 HSERDY 永远不置位，
     *    没有超时的话固件就永久卡在这里，且表现和"板子烧坏了"一模一样。 */
    RCC->CR |= RCC_CR_HSEON;
    for (guard = 0; (RCC->CR & RCC_CR_HSERDY) == 0; guard++) {
        if (guard > WAIT_GUARD) {
            RCC->CR &= ~RCC_CR_HSEON;
            return 0;
        }
    }

    /* 2. Flash 等待周期：72MHz 需要 2 个，并开预取 */
    FLASH->ACR = FLASH_ACR_PRFTBE | FLASH_ACR_LATENCY_2;

    /* 3. 总线分频：AHB=72M，APB1=36M（F1 规定 APB1 上限 36M），APB2=72M；
     *    PLL 源 = HSE（8MHz，不分频），倍频 ×9 → 72MHz */
    RCC->CFGR = RCC_CFGR_HPRE_DIV1 | RCC_CFGR_PPRE1_DIV2 | RCC_CFGR_PPRE2_DIV1 |
                RCC_CFGR_PLLSRC | RCC_CFGR_PLLMULL9;

    /* 4. 开 PLL、等锁定 */
    RCC->CR |= RCC_CR_PLLON;
    for (guard = 0; (RCC->CR & RCC_CR_PLLRDY) == 0; guard++) {
        if (guard > WAIT_GUARD) {
            RCC->CR &= ~RCC_CR_PLLON;
            return 0;
        }
    }

    /* 5. 切到 PLL 并确认切换完成 */
    RCC->CFGR |= RCC_CFGR_SW_PLL;
    for (guard = 0; (RCC->CFGR & RCC_CFGR_SWS) != RCC_CFGR_SWS_PLL; guard++) {
        if (guard > WAIT_GUARD) {
            return 0;
        }
    }

    return 1;
}
#endif /* USE_PLL */

void USART1_IRQHandler(void)
{
    uint32_t sr = USART1->SR;
    if (sr & (USART_SR_RXNE | USART_SR_ORE | USART_SR_PE)) {
        uint8_t byte = (uint8_t)(USART1->DR & 0xFFu); /* 读 DR 同时清 RXNE/ORE/PE */
        /* 校验错的字节直接丢：宁可少收一个字节（上层有"没回 OK 就重发"的余地），
         * 也不要把翻位后的脏数据当成合法命令执行 —— 后者可能让机构乱动。 */
        if ((sr & USART_SR_RXNE) && ((sr & USART_SR_PE) == 0)) {
            uint32_t next = (s_rx_head + 1u) & (RXBUF_SIZE - 1u);
            if (next != s_rx_tail) { /* 满了就丢新字节，不覆盖老数据 */
                s_rx[s_rx_head] = byte;
                s_rx_head = next;
            }
        }
    }
}

int uart_getbyte(void)
{
#if UART_RX_IRQ
    if (s_rx_tail == s_rx_head) {
        return -1;
    }
    uint8_t byte = s_rx[s_rx_tail];
    s_rx_tail = (s_rx_tail + 1u) & (RXBUF_SIZE - 1u);
    return (int)byte;
#else
    /* 轮询版：M1 用这个。读 SR 再读 DR 会清 RXNE/ORE/PE 三个标志。 */
    uint32_t sr = USART1->SR;
    if ((sr & USART_SR_RXNE) == 0) {
        return -1;
    }
    uint8_t byte = (uint8_t)(USART1->DR & 0xFFu);
    if (sr & USART_SR_PE) {
        return -1; /* 偶校验错的字节丢弃，理由同中断版：脏数据不能当命令执行 */
    }
    return (int)byte;
#endif
}

/* --------------------------------------------------------------------------
 * 波特率自校准 —— 让波特率与"主频到底是多少"解耦
 *
 * 为什么必须做：芯片内部 HSI 的规格是 ±1%（实测这块板约偏 1.5%）。按"正好 8MHz"
 * 硬算出来的 BRR 会让波特率偏差 1.5%，而 8E1（11 位帧）的容差只有 ±1.8% 左右
 * —— 正好卡在边缘：UART 每个起始位重新同步、采样点每 3~4 个字符漂移半个位，
 * 于是**偶发错帧**、表现为"偶尔乱几个字符"。
 *
 * 原理（不需要知道主频）：**BRR 的物理含义就是"一个 bit 占多少个 HCLK 周期"**
 *   BRR = USARTDIV×16 = f_cpu/(16×baud)×16 = f_cpu/baud
 * 所以只要量出主机发来的一串字节"每个 bit 平均占多少 HCLK 周期"，那就是该写的 BRR。
 *
 * 关键的抗错帧设计：**不去数"收到几个字节"**（波特率失配时接收端会错帧，
 * 数出来的个数是错的）。改成量整束的起止时长 span（HCLK 周期），再由
 *   brr = span / ((CAL_BYTES - 1) × 11)
 * 其中 **CAL_BYTES 是主机固定发过来的字节数（约定值，不是数出来的）**。
 *
 * ⚠️ 曾经踩过的坑（2026-09-22，实测 BRR 仍是名义值 69 才发现）：
 *   我一度想"不依赖约定值"、改用名义 BRR 反推字节数 n = span/(11×BRR_nominal)，
 *   结果 **n 的估计有系统性偏置**：BRR_真/BRR_nominal = 1.017 时，
 *   n = 63×1.017 = 64.1 取整成 64（本应 63），于是
 *   brr = 63×70.2/64 = 69.1 → 69 —— **偏差正好被抵消，等于白校准**。
 *   **教训：能约定一个常量就别去估它；"自洽的估计"在整数取整处最容易骗过自己。**
 *
 * 代价：启动时最多等 AUTOCAL_WAIT 次循环。没收到就沿用名义值。
 * 量产版本可以把校准结果存进 flash，之后就不必每次都等。
 * ------------------------------------------------------------------------ */
#define CAL_BYTES 64u         /* 主机每次校准固定发这么多字节（必须与 PC 侧一致！） */
#define AUTOCAL_WAIT 10000000u /* 等第一个字节的循环上限（本次调试临时放到约 10s） */
#define AUTOCAL_IDLE 120000u  /* 判定"一束结束"的静默循环数 */

static void uart_autocalibrate(void) __attribute__((unused)); /* AUTOCAL=0 时会被优化掉，别报 unused 警告 */
static void uart_autocalibrate(void)
{
    uint32_t t_first = 0;
    uint32_t t_last = 0;
    uint32_t got = 0;
    uint32_t idle = 0;
    uint32_t spin;

    SysTick->LOAD = 0xFFFFFFu; /* 自由运行当周期计数器（systick_init 之后会重配） */
    SysTick->VAL = 0;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    USART1->CR1 |= USART_CR1_RE;

    for (spin = 0; spin < AUTOCAL_WAIT; spin++) {
        if (USART1->SR & USART_SR_RXNE) {
            (void)USART1->DR;
            t_first = SysTick->VAL;
            t_last = t_first;
            got = 1;
            break;
        }
    }

    if (got != 0u) {
        for (;;) {
            if (USART1->SR & USART_SR_RXNE) {
                (void)USART1->DR;
                t_last = SysTick->VAL;
                got++;
                idle = 0;
            } else if (++idle > AUTOCAL_IDLE) {
                break; /* 静默够了，认为整束结束 */
            }
        }
    }

    SysTick->CTRL = 0; /* 交还给 systick_init */

    if (got >= 8u) {
        uint32_t span = (t_first - t_last) & 0xFFFFFFu; /* 整束占用的 HCLK 周期数 */
        /* span 对应 (CAL_BYTES-1) 个字节周期，每个 11 位 → brr = span/((N-1)×11) */
        uint32_t brr = span / ((CAL_BYTES - 1u) * 11u);

        if (brr >= 4u && brr <= 0xFFFFu) {
            USART1->CR1 = 0;
            USART1->BRR = (uint16_t)brr; /* ← 这才真正对上主机的速率 */
            USART1->CR1 = UART_CR1_8E1_RX;
            return;
        }
    }

    /* 没校准成功（主机没发东西）：保留名义值，正常启动即可 */
    USART1->CR1 = UART_CR1_8E1_RX;
}

/* --------------------------------------------------------------------------
 * M2 —— 舵机 PWM（TIM3_CH3 = PB0 给 Z 轴，TIM3_CH4 = PB1 给夹爪，50Hz）
 *
 * 为什么用**硬件 PWM**：舵机要求 20ms 周期里一个 0.5~2.5ms 的高电平，
 * 用软件翻引脚会被任何其它任务抖掉，硬件 PWM 一旦配置好就完全不需要 CPU。
 *
 * 分辨率：把 TIM3 的计数频率做成 **1MHz**（1 个 tick = 1µs），
 * 则 CCR = 脉宽µs 直接写入，ARR = 20000 就是 20ms。
 * 1µs ≈ 0.135°（270° 舵机），远超舵机自身的死区，够用。
 *
 * ⚠️ TIM3 挂在 APB1，而 **APB1 预分频≠1 时定时器时钟会翻倍**：
 *    本项目两种时钟配置下都恰好等于 SYSCLK ——
 *      HSI/HSE 8MHz：PPRE1=/1 → PCLK1=8MHz，TIM 时钟 = 8MHz
 *      PLL 72MHz  ：PPRE1=/2 → PCLK1=36MHz，TIM 时钟 = 36×2 = 72MHz
 *    所以 PSC 直接用 s_cpu_hz 算。将来若改 PPRE1，这里必须跟着改。
 * ------------------------------------------------------------------------ */
#define TIM3_TICK_HZ 1000000u

static uint16_t s_z_us = SERVO_SAFE_Z_US;
static uint16_t s_grip_us = SERVO_SAFE_GRIP_US;

static uint16_t servo_clamp(uint16_t us)
{
    if (us < SERVO_MIN_US) {
        return SERVO_MIN_US;
    }
    if (us > SERVO_MAX_US) {
        return SERVO_MAX_US;
    }
    return us;
}

void servo_init(void)
{
    RCC->APB1ENR |= RCC_APB1ENR_TIM3EN;
    RCC->APB2ENR |= RCC_APB2ENR_IOPBEN | RCC_APB2ENR_AFIOEN;

    /* 保险①：明确清掉 TIM3 重映射。
     * 不重映射时 CH3=PB0 / CH4=PB1；一旦 MAPR 里 TIM3_REMAP != 00，
     * 输出就跑到了 PC8/PC9 之类的地方 —— 引脚上量不到任何东西，
     * 而固件这边寄存器看起来完全正常，极难查。（复位默认是 0，但用串口 ISP
     * 的 Go 跳进应用时，前序 ROM bootloader 的 AFIO 设置是会被继承的。） */
    AFIO->MAPR &= ~AFIO_MAPR_TIM3_REMAP;

    /* PB0/PB1 → 复用推挽输出 50MHz。CRL 每 4 位管一个引脚（引脚 0~7），
     * 0xB = MODE 11(50MHz) + CNF 10(复用推挽) → PB0=bit[3:0], PB1=bit[7:4]。 */
    GPIOB->CRL = (GPIOB->CRL & ~0x000000FFu) | 0x000000BBu;

    TIM3->PSC = (uint16_t)(s_cpu_hz / TIM3_TICK_HZ - 1u);
    TIM3->ARR = (20000u) - 1u; /* 20ms 周期 = 50Hz */

    /* CH3/CH4 都配成 PWM 模式 1（CNT < CCR 时输出高）+ 预装载。
     * CCMR2 里 CH3 用 bit[6:4]=OC3M、bit3=OC3PE；CH4 用 bit[14:12]=OC4M、bit11=OC4PE。 */
    TIM3->CCMR2 = (6u << 4) | (1u << 3) | (6u << 12) | (1u << 11);
    TIM3->CCR3 = s_z_us;
    TIM3->CCR4 = s_grip_us;

    /* 保险②：手动产生一次更新事件，把 ARR/CCRx 的预装载值立刻搬进影子寄存器。
     * 开了 ARPE/OCxPE 之后，只写寄存器是不会马上生效的，要等一次 UEV；
     * 靠"计数器自然溢出"也能等到（就是 20ms 后），但这里显式触发，
     * 保证上电后**第一个周期**就是正确脉宽，不留"第一下不对"的窗口。 */
    TIM3->EGR = TIM_EGR_UG;

    TIM3->CCER = TIM_CCER_CC3E | TIM_CCER_CC4E;
    TIM3->CR1 = TIM_CR1_ARPE | TIM_CR1_CEN;
}

void servo_set_z(uint16_t us)
{
    s_z_us = servo_clamp(us);
    TIM3->CCR3 = s_z_us;
}

void servo_set_grip(uint16_t us)
{
    s_grip_us = servo_clamp(us);
    TIM3->CCR4 = s_grip_us;
}

/* --------------------------------------------------------------------------
 * M3 —— X/Y 步进脉冲发生器（软件生成，TIM4 定时中断 + 两轴相位累加）
 * 设计说明见 board.h 的 M3 段落；这里只讲实现要点。
 *
 * 相位累加：每个 ISR tick 给 s_st_acc 加 s_st_rate（Q16 定点，65536 = 每 tick 走半步）。
 *   累加满 → 翻转 PUL 电平一次。**一步 = 两次翻转**（上升+下降），
 *   在下降沿把位置 +1，这样"发出去的脉冲数"就是开环位置，且停止时 PUL 一定是低。
 *
 * 速率换算：s_st_rate = hz × 65536 / STEP_ISR_HZ
 *   hz = 1000、ISR = 10000 → rate = 6553 → 每 10 个 tick 翻转一次 = 5 个 tick 一个完整脉冲
 *   → 1kHz ✓（最大 5000Hz = rate 32768 = 每 2 tick 一个完整脉冲，已到上限）
 * ------------------------------------------------------------------------ */
#define ST_PUL_X (1u << 0)      /* PA0 */
#define ST_DIR_X (1u << 1)      /* PA1 */
#define ST_EN_X  (1u << 4)      /* PA4 */
#define ST_PUL_Y (1u << 5)      /* PA5 */
#define ST_DIR_Y (1u << 6)      /* PA6 */
#define ST_EN_Y  (1u << 7)      /* PA7 */

#define ST_ACC_FULL 65536u      /* Q16 满量程 */

static volatile int32_t  s_st_pos[2];    /* 开环位置 = 已发脉冲数（带方向） */
static volatile uint32_t s_st_half[2];   /* 剩余半周期数 = 2×剩余步数；0 = 该轴空闲 */
static volatile uint32_t s_st_rate[2];   /* 每 tick 累加量（当前值，加减速会改它） */
static volatile uint32_t s_st_acc[2];
static volatile uint8_t  s_st_lvl[2];
static volatile int8_t   s_st_dir[2];
static uint32_t s_st_hz = 4000u;         /* 默认目标步频（2026-10-04 用户要求 800→4000 = 50mm/s） */
static uint8_t  s_st_en = 0u;
static volatile uint32_t s_st_ticks;     /* 诊断：TIM4 中断次数（不动却在涨 = 中断在跑） */

/* ---- M3b：梯形加减速的状态（每个"下降沿 = 一步"时更新） ---- */
static volatile uint32_t s_st_total[2];  /* 本次移动总步数 */
static volatile uint32_t s_st_done[2];   /* 已走步数 */
static volatile uint32_t s_st_ramp[2];   /* 本次的加速段步数（= 减速段步数，对称） */
static volatile uint32_t s_st_delta[2];  /* 每步速率增量（Q16） */
static volatile uint32_t s_st_start[2];  /* 起步速率（Q16），减速回到它 */
/* ---- M3b：毫米换算与软限位（不参与中断，改动时都在停止状态） ---- */
static uint32_t s_st_ramp_cfg = STEP_DEFAULT_RAMP;
static uint32_t s_st_smm = STEP_DEFAULT_SMM;
static uint8_t  s_st_lim_on[2];
static int32_t  s_st_lim_min[2];         /* 单位：步（命令里用 mm 输入） */
static int32_t  s_st_lim_max[2];

void step_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;
    RCC->APB1ENR |= RCC_APB1ENR_TIM4EN;

    /* PA0/PA1/PA4/PA5/PA6/PA7 → 通用推挽输出 50MHz（0x3 = MODE 11 + CNF 00）
     * ⚠️ 2026-10-03 实测抓到的 bug：屏蔽字原来写成 `~0xFF0F00FF`，**漏掉了 PA5（bit20~23）** →
     *    PA5 保持复位值 0x4（浮空输入）→ **Y 轴 PUL 根本推不动**。
     *    正确屏蔽 = `0xFFFF00FF`：清 PA0/PA1（bit0~7）+ PA4~PA7（bit16~31），
     *    **保留 bit8~15**（PA2/PA3 = USART2，别动！）。判据：`M3?` 的 acrl 应 = 0x33330033。 */
    GPIOA->CRL = (GPIOA->CRL & ~0xFFFF00FFu) | 0x33330033u;

    /* 上电默认：PUL **低**、DIR 低（正方向）、EN **失能**（电机自由，避免插上就乱跑）
     * ⚠️ 同一天抓到的第二个 bug：原来把 PUL/DIR 也 BSRR **置位**了（应为清零）→ aodr 里 PA0/PA1=1。
     *    BSRR 低 16 位置位、高 16 位清零，别写反。判据：`M3?` 的 aodr 低 8 位应为 0b11110000（EN 高）。 */
    GPIOA->BSRR = (ST_PUL_X | ST_PUL_Y | ST_DIR_X | ST_DIR_Y) << 16;
    if (STEP_EN_ACTIVE_LOW) {
        GPIOA->BSRR = ST_EN_X | ST_EN_Y;          /* 低有效 → 拉高 = 失能 */
    }

    /* TIM4 挂在 APB1。⚠️ 与 TIM3 同样的约定：PPRE1==1 时定时器时钟 = s_cpu_hz。
     *
     * ⚠️⚠️ 2026-10-03 实测踩坑（花了几个来回才定位）：
     *    原来写成 `PSC = s_cpu_hz/STEP_ISR_HZ - 1; ARR = 0;`，想着"ARR=0 → 每来一个计数就溢出一次"。
     *    结果：**中断只在开机那次（EGR=UG 手动触发）进过一次，之后再也不进** ✗ ——
     *    判据是 `M3?` 里的 `ticks=` 一直停在 1、而 `tim4cr1=129`（CEN 确实在跑）。
     *    即：**这片 F103 上 ARR=0 不产生计数器溢出**，于是 UIF 永远不再置位。
     *    正确配法 = 教科书式：**PSC=0 + ARR=时钟/频率-1**（下面这样）。 */
    TIM4->PSC = 0u;
    TIM4->ARR = (uint16_t)(s_cpu_hz / STEP_ISR_HZ - 1u);
    TIM4->DIER = TIM_DIER_UIE;
    TIM4->EGR = TIM_EGR_UG;      /* 立刻把 PSC/ARR 装进影子寄存器 */
    TIM4->SR = 0u;
    TIM4->CR1 = TIM_CR1_ARPE;    /* 先不启动：只在运动时 CEN=1，省 CPU */

    /* 优先级 0（比串口的 1/2 高）：脉冲时序不能被串口中断拖偏 */
    NVIC_SetPriority(TIM4_IRQn, 0);
    NVIC_EnableIRQ(TIM4_IRQn);
}

void TIM4_IRQHandler(void)
{
    uint32_t i;
    if (!(TIM4->SR & TIM_SR_UIF)) {
        return;
    }
    TIM4->SR = (uint16_t)~TIM_SR_UIF;
    s_st_ticks++;                        /* 诊断用：进一次中断加一 */

    for (i = 0u; i < 2u; i++) {
        uint32_t bit;
        if (s_st_half[i] == 0u) {
            continue;
        }
        s_st_acc[i] += s_st_rate[i];
        if (s_st_acc[i] < ST_ACC_FULL) {
            continue;
        }
        s_st_acc[i] -= ST_ACC_FULL;

        if (s_st_lvl[i]) {
            s_st_lvl[i] = 0u;                 /* 下降沿 = 一个完整脉冲走完 */
            s_st_pos[i] += (int32_t)s_st_dir[i];

            /* ---- M3b 梯形加减速：**按已走步数**线性改速率 ----
             * 前 ramp 步加速；后 ramp 步减速（对称）；中间的匀速段速率保持不变。
             * 短距离时 s_st_ramp 已被钳到 total/2 → 自然退化成三角形，不会"没加速完就该减速"。 */
            s_st_done[i]++;
            if (s_st_done[i] < s_st_ramp[i]) {
                s_st_rate[i] += s_st_delta[i];
            } else if (s_st_done[i] + s_st_ramp[i] >= s_st_total[i]) {
                if (s_st_rate[i] > s_st_start[i]) {
                    if (s_st_rate[i] > s_st_start[i] + s_st_delta[i]) {
                        s_st_rate[i] -= s_st_delta[i];
                    } else {
                        s_st_rate[i] = s_st_start[i];
                    }
                }
            }
        } else {
            s_st_lvl[i] = 1u;
        }
        bit = (i == 0u) ? ST_PUL_X : ST_PUL_Y;
        GPIOA->BSRR = s_st_lvl[i] ? bit : (bit << 16);
        s_st_half[i]--;
    }

    /* 两轴都停了就把定时器关掉（否则 10kHz 中断白烧 CPU） */
    if (s_st_half[0] == 0u && s_st_half[1] == 0u) {
        TIM4->CR1 &= ~TIM_CR1_CEN;
    }
}

void step_enable(int on)
{
    uint32_t bits = ST_EN_X | ST_EN_Y;
    int level;
    s_st_en = on ? 1u : 0u;
#if STEP_EN_ACTIVE_LOW
    level = on ? 0 : 1;          /* 低有效：拉低 = 使能 */
#else
    level = on ? 1 : 0;
#endif
    if (level) {
        GPIOA->BSRR = bits;
    } else {
        GPIOA->BSRR = (bits << 16);
    }
}

/* 返回值：0 已启动；-1 轴号错；-2 步数为 0；-3 该轴还在动；-4 未使能（先发 EN 1） */
int step_jog(int axis, int32_t steps, uint32_t hz)
{
    uint32_t bit, n;
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return -1;
    }
    if (steps == 0) {
        return -2;
    }
    if (s_st_half[axis] != 0u) {
        return -3;
    }
    if (!s_st_en) {
        return -4;
    }
    if (hz == 0u) {
        hz = s_st_hz;
    }
    if (hz > STEP_MAX_HZ) {
        hz = STEP_MAX_HZ;
    }
    if (hz < 10u) {
        hz = 10u;
    }

    n = (uint32_t)((steps > 0) ? steps : -steps);

    /* ---- M3b：软限位检查（只在开启时） ----
     * 用"目标绝对位置"判，不判方向 —— 往限位外走一步就拒，避免"走一半才发现"。 */
    if (s_st_lim_on[axis]) {
        int32_t target = s_st_pos[axis] + steps;
        if (target < s_st_lim_min[axis] || target > s_st_lim_max[axis]) {
            return -5;
        }
    }

    /* 先定方向，再留 DIR 建立时间（驱动器要求 DIR 先于 PUL ≥5µs，这里给足 ~1ms） */
    s_st_dir[axis] = (steps > 0) ? 1 : -1;
    bit = (axis == STEP_AXIS_X) ? ST_DIR_X : ST_DIR_Y;
    GPIOA->BSRR = (s_st_dir[axis] > 0) ? bit : (bit << 16);
    {
        volatile uint32_t d;
        for (d = 0u; d < 2000u; d++) {
            __NOP();
        }
    }

    /* ---- M3b：装配梯形型线 ----
     * rate/delta 的单位都是 Q16（65536 = 每个 ISR tick 走半步 = 5kHz 满速）。
     * 加速段与减速段等长、都取 min(配置 ramp, 总步数/2) → 短距离自动变三角形。 */
    {
        uint32_t target = (hz << 16) / STEP_ISR_HZ;
        uint32_t start = (STEP_START_HZ << 16) / STEP_ISR_HZ;
        uint32_t ramp = s_st_ramp_cfg;

        if (start > target) {
            start = target;              /* 目标比起步还慢 → 不加速 */
        }
        if (ramp > (n / 2u)) {
            ramp = n / 2u;
        }
        /* ⚠️ 2026-10-03 回归套件抓到的 bug：ramp=0（`ACC 0` 或总步数只有 1 步）时，
         *    原来仍然把速率设成**起步频率**，而抬到目标速度靠的是"加速段的 delta" ——
         *    ramp=0 就没有加速段 → 速率永远停在 200Hz ✗（实测 10mm 要走十几秒）。
         *    正解：ramp=0 就直接用目标速率（= 真正"不做加减速"的语义）。 */
        s_st_start[axis] = (ramp != 0u) ? start : target;
        s_st_rate[axis] = (ramp != 0u) ? start : target;
        s_st_ramp[axis] = ramp;
        s_st_delta[axis] = (ramp != 0u) ? ((target - start) / ramp) : 0u;
        s_st_total[axis] = n;
        s_st_done[axis] = 0u;
    }

    s_st_acc[axis] = 0u;
    s_st_lvl[axis] = 0u;
    bit = (axis == STEP_AXIS_X) ? ST_PUL_X : ST_PUL_Y;
    GPIOA->BSRR = (bit << 16);               /* PUL 从低起步 */

    /* 先备好一切，**最后**才写 s_st_half —— ISR 只在它非 0 时才动这一轴，天然避免半配置状态 */
    s_st_half[axis] = n * 2u;

    TIM4->CR1 |= TIM_CR1_CEN;
    return 0;
}

/* ---- M3b：毫米换算 / 零点 / 软限位 ---- */

void step_set_ramp(uint32_t steps)
{
    if (steps > 100000u) {
        steps = 100000u;
    }
    s_st_ramp_cfg = steps;
}

uint32_t step_ramp(void)
{
    return s_st_ramp_cfg;
}

void step_set_smm(uint32_t steps_per_mm)
{
    if (steps_per_mm < 1u) {
        steps_per_mm = 1u;
    }
    if (steps_per_mm > 100000u) {
        steps_per_mm = 100000u;
    }
    s_st_smm = steps_per_mm;
}

uint32_t step_smm(void)
{
    return s_st_smm;
}

/* mm → 步。用 int32 乘法再除，避免 step_jog 里先乘后溢出 */
int step_move_mm(int axis, int32_t mm, uint32_t hz)
{
    int32_t steps;
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return -1;
    }
    if (mm > 100000 || mm < -100000) {
        return -6;                        /* 明显的离谱值，直接拒 */
    }
    steps = mm * (int32_t)s_st_smm;
    if (steps == 0) {
        return -2;
    }
    return step_jog(axis, steps, hz);
}

void step_zero(int axis)
{
    if (axis == STEP_AXIS_X || axis == STEP_AXIS_Y) {
        s_st_pos[axis] = 0;
    } else {
        s_st_pos[0] = 0;
        s_st_pos[1] = 0;
    }
}

void step_set_limit(int axis, int32_t min_mm, int32_t max_mm)
{
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return;
    }
    if (min_mm > max_mm) {
        int32_t t = min_mm;
        min_mm = max_mm;
        max_mm = t;
    }
    s_st_lim_min[axis] = min_mm * (int32_t)s_st_smm;
    s_st_lim_max[axis] = max_mm * (int32_t)s_st_smm;
    s_st_lim_on[axis] = 1u;
}

void step_clear_limit(int axis)
{
    if (axis == STEP_AXIS_X || axis == STEP_AXIS_Y) {
        s_st_lim_on[axis] = 0u;
    } else {
        s_st_lim_on[0] = 0u;
        s_st_lim_on[1] = 0u;
    }
}

int step_limit_on(int axis)
{
    return (axis == STEP_AXIS_X || axis == STEP_AXIS_Y) ? (int)s_st_lim_on[axis] : 0;
}

int32_t step_limit_min_mm(int axis)
{
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return 0;
    }
    return s_st_lim_min[axis] / (int32_t)s_st_smm;
}

int32_t step_limit_max_mm(int axis)
{
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return 0;
    }
    return s_st_lim_max[axis] / (int32_t)s_st_smm;
}

void step_stop(void)
{
    s_st_half[0] = 0u;
    s_st_half[1] = 0u;
    s_st_lvl[0] = 0u;
    s_st_lvl[1] = 0u;
    GPIOA->BSRR = (ST_PUL_X | ST_PUL_Y) << 16;   /* PUL 都拉低 */
    TIM4->CR1 &= ~TIM_CR1_CEN;
}

uint8_t step_busy(void)
{
    return (s_st_half[0] != 0u || s_st_half[1] != 0u) ? 1u : 0u;
}

int32_t step_pos(int axis)
{
    if (axis != STEP_AXIS_X && axis != STEP_AXIS_Y) {
        return 0;
    }
    return s_st_pos[axis];
}

uint32_t step_rate_hz(void)
{
    return s_st_hz;
}

/* 诊断三件套：中断次数 / 当前速率 / 剩余半周期（判断"中断在不在跑"用） */
uint32_t step_ticks(void)
{
    return s_st_ticks;
}

uint32_t step_rate_now(int axis)
{
    return (axis == STEP_AXIS_X || axis == STEP_AXIS_Y) ? s_st_rate[axis] : 0u;
}

uint32_t step_half_now(int axis)
{
    return (axis == STEP_AXIS_X || axis == STEP_AXIS_Y) ? s_st_half[axis] : 0u;
}

void step_set_rate(uint32_t hz)
{
    if (hz > STEP_MAX_HZ) {
        hz = STEP_MAX_HZ;
    }
    if (hz < 10u) {
        hz = 10u;
    }
    s_st_hz = hz;
}

/* --------------------------------------------------------------------------
 * M2 —— 入盒红外（6 路，内部上拉，**低电平 = 有货挡光**）
 *
 * ⚠️ 2026-10-04 换板（RCT6 → C8T6）**引脚全部重排** ✗：
 *    C8T6 是 48 脚，**根本没有 PC0~PC12**（只有 PC13~15 且被晶振/复位相关占用）✗，
 *    所以红外从原来的 `PC0~PC5` 挪到下面这 6 个脚：
 *      **盒1=PB12 · 盒2=PB13 · 盒3=PB14 · 盒4=PB15 · 盒5=PA8 · 盒6=PA11**
 *    （PB12~PB15 本来就空着 ✓；PA8 空着 ✓；PA11 = USB D−，本装置不用 USB 数据 ✓ 可当 GPIO ✓）
 *    配法：**上拉输入** = MODE 00 + CNF 10 → 每半字节 0x8，且 ODR 对应位写 1 选上拉。
 * ⚠️ 供电：红外接收模块仍建议 **3.3V**（PA11/PB12~15 都是 FT 脚，但统一 3.3V 最省心 ✓）。
 * ------------------------------------------------------------------------ */
void box_init(void)
{
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN | RCC_APB2ENR_IOPBEN;

    /* PB12~PB15 → 上拉输入（CRH 管引脚 8~15：PB12 在 bit[19:16] … PB15 在 bit[31:28]） */
    GPIOB->CRH = (GPIOB->CRH & ~0xFFFF0000u) | 0x88880000u;
    GPIOB->ODR |= 0xF000u;               /* ODR=1 → 上拉 */

    /* PA8 与 PA11 → 上拉输入（PA8 在 CRH bit[3:0]，PA11 在 bit[15:12]） */
    GPIOA->CRH = (GPIOA->CRH & ~0x0000F00Fu) | 0x00008008u;
    GPIOA->ODR |= (1u << 8) | (1u << 11);
}

uint8_t box_read(void)
{
    uint8_t m = 0u;

    /* 低电平 = 有货挡光 → 取反后置位，让"1 = 有货"更符合直觉（位0..5 = 盒1..6） */
    if ((GPIOB->IDR & (1u << 12)) == 0u) { m |= (uint8_t)(1u << 0); }
    if ((GPIOB->IDR & (1u << 13)) == 0u) { m |= (uint8_t)(1u << 1); }
    if ((GPIOB->IDR & (1u << 14)) == 0u) { m |= (uint8_t)(1u << 2); }
    if ((GPIOB->IDR & (1u << 15)) == 0u) { m |= (uint8_t)(1u << 3); }
    if ((GPIOA->IDR & (1u << 8)) == 0u)  { m |= (uint8_t)(1u << 4); }
    if ((GPIOA->IDR & (1u << 11)) == 0u) { m |= (uint8_t)(1u << 5); }
    return m;
}

/* --------------------------------------------------------------------------
 * M2+ —— 串口屏（淘晶驰 USART HMI）→ **USART2**，PA2=TX / PA3=RX
 *
 * ⚠️ 2026-10-04 的两步变化：
 *   ① 换 C8T6（只有 USART1/2/3 ✗：USART1=调试口、USART3=视觉 K210）→ 屏一度改成
 *      **软件串口 PB8**（只能发、波特率锁死 9600 ✗）；
 *   ② 用户决定**不再用无线桥 ESP32-C6** ✗ ⇒ **USART2 空出来了** ✓
 *      ⇒ 屏**改回硬件串口 USART2** ✓（能收发、波特率可切、不再占 CPU 空转 ✓）
 *   本版结论：**PA2/PA3 = 串口屏**，PB8 闲置 ✓。
 *
 * 只做**透传**（《设计》§7）：固件不解释屏的指令集，让上位机直接写 —— 改界面不动固件。
 * ⚠️ 屏侧 8N1（**不开校验**），与 USART1 的 8E1 不同。
 * ⚠️ 波特率必须与屏一致（出厂常见 9600）→ `LCDBAUD <n>` 可运行时切 ✓。
 * ------------------------------------------------------------------------ */
#define LCD_RXBUF_SIZE 64u /* 必须是 2 的幂 */

static uint8_t  s_lcd_rx[LCD_RXBUF_SIZE];
static uint32_t s_lcd_head;
static uint32_t s_lcd_tail;
static uint32_t s_lcd_baud_hz = 9600u;

/* USART2 挂在 **APB1**：BRR 要用 PCLK1，不是 HCLK。
 * ⚠️ 当前 HSI/HSE 8MHz 时 PPRE1=/1 → PCLK1 = 8MHz（和 PCLK2 相同，容易蒙对）；
 *    一旦 M3 上 PLL 72MHz，PPRE1 会变成 /2 → PCLK1 = 36MHz，
 *    这时若照抄 USART1 的算法（用 s_cpu_hz）**波特率会差一倍**。所以直接从 RCC->CFGR 推。 */
static uint32_t pclk1_hz(void)
{
    uint32_t pre = (RCC->CFGR & RCC_CFGR_PPRE1) >> 8;

    if (pre < 4u) {
        return s_cpu_hz;            /* 0xx = /1 */
    }
    return s_cpu_hz >> (pre - 3u);  /* 100→/2, 101→/4, 110→/8, 111→/16 */
}

void lcd_init(uint32_t baud)
{
    uint32_t hz = baud ? baud : 9600u;

    RCC->APB1ENR |= RCC_APB1ENR_USART2EN;
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;

    /* PA2 = 复用推挽输出 50MHz（USART2_TX）；PA3 = 浮空输入（USART2_RX）
     * CRL 每 4 位管一个引脚：PA2 在 bit[11:8] → 0xB，PA3 在 bit[15:12] → 0x4 */
    GPIOA->CRL = (GPIOA->CRL & ~0x0000FF00u) | 0x00004B00u;

    USART2->CR1 = 0; /* 改配置前先关 UE，避免半个字节的毛刺 */
    USART2->BRR = (pclk1_hz() + hz / 2u) / hz;
    USART2->CR2 = 0;
    USART2->CR3 = 0;
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE; /* 8N1 */

    s_lcd_baud_hz = hz;
    s_lcd_head = 0;
    s_lcd_tail = 0;
}

uint32_t lcd_baud(void)
{
    return s_lcd_baud_hz;
}

static void lcd_put_byte(uint8_t byte)
{
    while ((USART2->SR & USART_SR_TXE) == 0) { }
    USART2->DR = (uint16_t)byte;
}

void lcd_send_bytes(const uint8_t *data, uint32_t len)
{
    uint32_t i;

    for (i = 0; i < len; i++) {
        lcd_put_byte(data[i]);
    }
    while ((USART2->SR & USART_SR_TC) == 0) { } /* 等最后一字节真正发完 */
}

uint32_t lcd_send_text(const char *text)
{
    uint32_t n = 0;

    while (*text != '\0') {
        lcd_put_byte((uint8_t)*text++);
        n++;
    }
    /* 淘晶驰（Nextion 系）：**每条指令必须以 FF FF FF 结尾**才会被屏执行 */
    lcd_put_byte(0xFFu);
    lcd_put_byte(0xFFu);
    lcd_put_byte(0xFFu);
    while ((USART2->SR & USART_SR_TC) == 0) { }
    return n + 3u; /* 返回实际字节数：没有屏时，这是验证"载荷有没有被截断"的唯一办法 */
}

/* 屏发回来的字节（触摸事件等）。用轮询收：主循环一圈只有几条指令，9600~115200 下
 * 一字节 87~1040µs，不容易丢；真丢了也只是调试信息，不影响判别"屏有没有在说话"。
 * ⚠️ 2026-10-04：屏现在走 **USART2**（见 lcd_init），所以这里读 USART2 ✓ */
void lcd_poll(void)
{
    while (USART2->SR & USART_SR_RXNE) {
        uint8_t byte = (uint8_t)(USART2->DR & 0xFFu);
        uint32_t next = (s_lcd_head + 1u) & (LCD_RXBUF_SIZE - 1u);

        if (next != s_lcd_tail) { /* 满了丢新字节，不覆盖老数据 */
            s_lcd_rx[s_lcd_head] = byte;
            s_lcd_head = next;
        }
    }
}

int lcd_getbyte(void)
{
    uint8_t byte;

    if (s_lcd_tail == s_lcd_head) {
        return -1;
    }
    byte = s_lcd_rx[s_lcd_tail];
    s_lcd_tail = (s_lcd_tail + 1u) & (LCD_RXBUF_SIZE - 1u);
    return (int)byte;
}

/* PB11 的输入方式切换 —— **判"屏的 TX 到底有没有接着"的客观办法**。
 *
 * 原理：浮空输入在悬空时也常常读到高电平，所以"idr 读到 1"**不能**证明线上有东西。
 *   改成**下拉输入**后：
 *     · 屏的 TX 接着（空闲时输出高）→ 它会顶住弱下拉 → idr 的 bit11 仍 **1** ⇒ 接上了
 *     · 什么都没接 → 下拉赢 → idr 的 bit11 = **0** ⇒ 那根线没通
 * 用完记得 `lcd_pulldown(0)` 恢复浮空，否则会影响正常接收。
 * （CRH 里 PB11 的 4 位在 bit[15:12]：0x4 = 浮空输入，0x8 = 输入+上/下拉，方向由 ODR 定） */
void lcd_pulldown(int enable)
{
    uint32_t crh = GPIOB->CRH;

    crh &= ~0x0000F000u;
    if (enable) {
        crh |= 0x00008000u;          /* 输入 + 上/下拉 */
        GPIOB->BRR = (1u << 11);     /* ODR bit11 = 0 → 下拉 */
    } else {
        crh |= 0x00004000u;          /* 浮空输入（正常接收用这个） */
    }
    GPIOB->CRH = crh;
}

/* --------------------------------------------------------------------------
 * M4 —— USART2：接「装置侧无线桥」ESP32-C6 的 UART1，作为**第二命令通道**
 *
 * 引脚：PA2 = USART2_TX（→ C6 GPIO5）、PA3 = USART2_RX（← C6 GPIO4）
 * 参数：**115200 8N1** ⚠️ 与 USART1 的 8E1 不同（C6 侧是 MicroPython 的
 *      `machine.UART(1, baudrate=115200)`，默认就是 8N1）。
 * 时钟：USART2 挂 **APB1** → BRR 用 PCLK1（复用串口屏那套 pclk1_hz()）；
 *      ⚠️ 现在是 8MHz 所以 PCLK1 = 8MHz 看着和 USART1 一样，**上 PLL 后就会分家**。
 * 收法：**接收中断 + 环形缓冲**（不用轮询）：发一条回复要阻塞好几毫秒
 *      （60 字节 @115200 ≈ 5.2ms），这期间 UART 那 2 字节缓冲早就溢出了。
 * 接线：`C6 GPIO4(TX) → PA3`、`C6 GPIO5(RX) ← PA2`、**GND 必须共地**。
 * ------------------------------------------------------------------------ */
#define U2_RXBUF_SIZE 128u /* 必须是 2 的幂 */
#define U2_BAUD 115200u

static volatile uint8_t  s_u2_rx[U2_RXBUF_SIZE];
static volatile uint32_t s_u2_head;
static volatile uint32_t s_u2_tail;
static volatile uint32_t s_u2_rx_n; /* 自开机以来收到的字节数（U2? 回读用） */
static volatile uint32_t s_u2_tx_n;

void u2_init(uint32_t baud)
{
    uint32_t hz = baud ? baud : U2_BAUD;

    RCC->APB1ENR |= RCC_APB1ENR_USART2EN; /* USART2 在 APB1，别写成 APB2 */
    RCC->APB2ENR |= RCC_APB2ENR_IOPAEN;

    /* CRL 每 4 位管一个引脚（0~7）：
     *     PA2 → bit[11:8]  = 0xB → 复用推挽输出 50MHz （USART2_TX）
     *     PA3 → bit[15:12] = 0x4 → 浮空输入            （USART2_RX）
     *   合起来 = (0xB << 8) | (0x4 << 12) = 0x4B00
     * ⚠️ USART1 那边曾把这两个半字节写反（PA9/PA10 对调），症状是"固件在跑但一个字节都不出"。 */
    GPIOA->CRL = (GPIOA->CRL & ~0x0000FF00u) | 0x00004B00u;

    USART2->CR1 = 0; /* 改配置前先关 UE */
    USART2->BRR = (pclk1_hz() + hz / 2u) / hz;
    USART2->CR2 = 0;
    USART2->CR3 = 0;
    /* **8N1**：不置 PCE、不置 M（与 C6 对齐；USART1 那条才是 8E1） */
    USART2->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;

    s_u2_head = 0;
    s_u2_tail = 0;
    s_u2_rx_n = 0;
    s_u2_tx_n = 0;

    /* 优先级比 USART1(1) 略低：调试口优先 */
    NVIC_SetPriority(USART2_IRQn, 2);
    NVIC_EnableIRQ(USART2_IRQn);
}

void USART2_IRQHandler(void)
{
    uint32_t sr = USART2->SR;

    if (sr & (USART_SR_RXNE | USART_SR_ORE | USART_SR_PE)) {
        uint8_t byte = (uint8_t)(USART2->DR & 0xFFu); /* 读 DR 同时清 RXNE/ORE/PE */
        if (sr & USART_SR_RXNE) {
            uint32_t next = (s_u2_head + 1u) & (U2_RXBUF_SIZE - 1u);
            if (next != s_u2_tail) { /* 满了丢新字节，不覆盖老数据 */
                s_u2_rx[s_u2_head] = byte;
                s_u2_head = next;
                s_u2_rx_n++;
            }
        }
    }
}

int u2_getbyte(void)
{
    uint8_t byte;

    if (s_u2_tail == s_u2_head) {
        return -1;
    }
    byte = s_u2_rx[s_u2_tail];
    s_u2_tail = (s_u2_tail + 1u) & (U2_RXBUF_SIZE - 1u);
    return (int)byte;
}

uint32_t u2_rx_count(void) { return s_u2_rx_n; }
uint32_t u2_tx_count(void) { return s_u2_tx_n; }

void u2_send_bytes(const uint8_t *data, uint32_t len)
{
    uint32_t i;

    for (i = 0; i < len; i++) {
        while ((USART2->SR & USART_SR_TXE) == 0) { }
        USART2->DR = (uint16_t)data[i];
        s_u2_tx_n++;
    }
    while ((USART2->SR & USART_SR_TC) == 0) { } /* 等最后一字节真正发完 */
}

void u2_send_text(const char *text)
{
    while (*text != '\0') {
        while ((USART2->SR & USART_SR_TXE) == 0) { }
        USART2->DR = (uint16_t)(uint8_t)(*text++);
        s_u2_tx_n++;
    }
    while ((USART2->SR & USART_SR_TC) == 0) { }
}

/* PA3 输入方式切换 —— 判"**C6 的 TX 到底有没有接到 PA3**"（思路同 LCDPD）。
 *
 * 原理：浮空输入在悬空时也常读到高电平，所以"idr 读到 1"**不能**证明线上有东西。
 *   改成**下拉输入**后：
 *     · C6 的 TX 接着（空闲时输出高）→ 它顶得住弱下拉 → idr 的 bit3 仍 **1** ⇒ 接上了
 *     · 什么都没接 → 下拉赢 → idr 的 bit3 = **0** ⇒ 那根线没通
 * 用完记得 `u2_pulldown(0)` 恢复浮空，否则影响正常接收。
 * （CRL 里 PA3 的 4 位在 bit[15:12]：0x4 = 浮空输入，0x8 = 输入+上/下拉，方向由 ODR 定） */
void u2_pulldown(int enable)
{
    uint32_t crl = GPIOA->CRL;

    crl &= ~0x0000F000u;
    if (enable) {
        crl |= 0x00008000u;      /* 输入 + 上/下拉 */
        GPIOA->BRR = (1u << 3);  /* ODR bit3 = 0 → 下拉 */
    } else {
        crl |= 0x00004000u;      /* 浮空输入（正常接收用这个） */
    }
    GPIOA->CRL = crl;
}

/* --------------------------------------------------------------------------
 * M4 —— 视觉板 CanMV/K210 的命令通道
 *
 * ⚠️ 2026-10-04 换板（RCT6 → C8T6）**改到了 USART3**：
 *    C8T6 是中容量，**没有 UART4/UART5** ✗；而 USART1=调试口、USART2=无线桥，
 *    所以视觉口改用 **USART3**，串口屏则让位去走软件串口（见上面 lcd_init）✓。
 *    **API 名字仍然叫 u4_*（py 侧/@ 协议不变 ✓）**，只是底层从 UART4 换成了 USART3 ✓。
 *
 * 引脚：**PB10 = USART3_TX**（→ K210 的 RXD）、**PB11 = USART3_RX**（← K210 的 TXD）
 *   ⚠️ PB10/PB11 在 **CRH**，每 4 位一个引脚：
 *      PB10 → bit[11:8]  = 0xB（复用推挽 50MHz）
 *      PB11 → bit[15:12] = 0x4（浮空输入）
 *      合起来 = 0x4B00 ✓
 * 时钟：USART3 挂 **APB1** → BRR 用 PCLK1 ✓
 * 中断号：**USART3_IRQn = 39** ⚠️ 向量表要 `[16 + 39]`（"加 16"这项目踩过三次了 ✗）
 * ------------------------------------------------------------------------ */
#define U4_RXBUF_SIZE 128u /* 2 的幂 */
#define U4_BAUD 115200u

static volatile uint8_t  s_u4_rx[U4_RXBUF_SIZE];
static volatile uint32_t s_u4_head;
static volatile uint32_t s_u4_tail;
static volatile uint32_t s_u4_rx_n;
static volatile uint32_t s_u4_tx_n;

void u4_init(uint32_t baud)
{
    uint32_t hz = baud ? baud : U4_BAUD;

    RCC->APB1ENR |= RCC_APB1ENR_USART3EN; /* 视觉口 = USART3（C8T6 没有 UART4） */
    RCC->APB2ENR |= RCC_APB2ENR_IOPBEN;

    GPIOB->CRH = (GPIOB->CRH & ~0x0000FF00u) | 0x00004B00u;

    USART3->CR1 = 0; /* 改配置前先关 UE */
    USART3->BRR = (pclk1_hz() + hz / 2u) / hz;
    USART3->CR2 = 0;
    USART3->CR3 = 0;
    /* **8N1**：不置 PCE、不置 M（与 K210 的 machine.UART 对齐） */
    USART3->CR1 = USART_CR1_UE | USART_CR1_TE | USART_CR1_RE | USART_CR1_RXNEIE;

    s_u4_head = 0;
    s_u4_tail = 0;
    s_u4_rx_n = 0;
    s_u4_tx_n = 0;

    /* 优先级 3：比调试口(1)/无线桥(2) 都低 —— 视觉是"辅助输入"，不该抢实时性 */
    NVIC_SetPriority(USART3_IRQn, 3);
    NVIC_EnableIRQ(USART3_IRQn);
}

void USART3_IRQHandler(void)
{
    uint32_t sr = USART3->SR;

    if (sr & (USART_SR_RXNE | USART_SR_ORE | USART_SR_PE)) {
        uint8_t byte = (uint8_t)(USART3->DR & 0xFFu); /* 读 DR 同时清 RXNE/ORE/PE */
        if (sr & USART_SR_RXNE) {
            uint32_t next = (s_u4_head + 1u) & (U4_RXBUF_SIZE - 1u);
            if (next != s_u4_tail) { /* 满了丢新字节 */
                s_u4_rx[s_u4_head] = byte;
                s_u4_head = next;
                s_u4_rx_n++;
            }
        }
    }
}

int u4_getbyte(void)
{
    uint8_t byte;

    if (s_u4_tail == s_u4_head) {
        return -1;
    }
    byte = s_u4_rx[s_u4_tail];
    s_u4_tail = (s_u4_tail + 1u) & (U4_RXBUF_SIZE - 1u);
    return (int)byte;
}

uint32_t u4_rx_count(void) { return s_u4_rx_n; }
uint32_t u4_tx_count(void) { return s_u4_tx_n; }

void u4_send_bytes(const uint8_t *data, uint32_t len)
{
    uint32_t i;

    for (i = 0; i < len; i++) {
        while ((USART3->SR & USART_SR_TXE) == 0) { }
        USART3->DR = (uint16_t)data[i];
        s_u4_tx_n++;
    }
    while ((USART3->SR & USART_SR_TC) == 0) { }
}

void u4_send_text(const char *text)
{
    while (*text != '\0') {
        while ((USART3->SR & USART_SR_TXE) == 0) { }
        USART3->DR = (uint16_t)(uint8_t)(*text++);
        s_u4_tx_n++;
    }
    while ((USART3->SR & USART_SR_TC) == 0) { }
}

void board_init(void)
{
    /* 先显式把时钟拉回已知状态（不继承 bootloader 留下的配置，理由见 clock_reset_to_hsi） */
    clock_reset_to_hsi();

    /* ⚠️ 2026-10-04 换 C8T6 后必须做的一步：**关掉 JTAG、只留 SWD** ✗
     *    C8T6 只有 48 脚，能用的 GPIO 很紧 ✗，而 **PA15/PB3/PB4 默认被 JTAG 占着**
     *    （JTDI/JTDO/NJTRST）—— 不关的话它们**当不了普通 GPIO**（配了也不动 ✗）。
     *    SWJ_CFG = 010（JTAG-DP disabled, SW-DP enabled）⇒ **ST-Link 仍能用** ✓✓
     *    （SWD 用的 PA13/PA14 保持原样 ✓）。
     *    换回 RCT6 时这一步也无害 ✓（可以留着）。 */
    RCC->APB2ENR |= RCC_APB2ENR_AFIOEN;
    AFIO->MAPR = (AFIO->MAPR & ~AFIO_MAPR_SWJ_CFG) | AFIO_MAPR_SWJ_CFG_JTAGDISABLE;

    uart_gpio_init();
    box_init();

    /* ⭐ 心跳灯：蓝板 PC13 的板载 LED（**低电平点亮** ✓）
     *   为什么加：换 C8T6 之后一度出现"程序烧进去了、但两条串口都没反应"的僵局 ✗ ——
     *   有一盏灯就能**一眼分清**是"芯片没跑我们的程序"还是"只是串口没通" ✓。
     *   闪烁 = 主循环在跑 ✓（不闪 = BOOT0/时钟/复位有问题 ✗）。
     *   ⚠️ 换回 RCT6 时这行也无害 ✓。 */
    RCC->APB2ENR |= RCC_APB2ENR_IOPCEN;
    GPIOC->CRH = (GPIOC->CRH & ~0x00F00000u) | 0x00200000u; /* PC13 = 推挽输出 2MHz */
    GPIOC->BSRR = (1u << 13);                               /* 先灭（高电平）*/

    /* 先用 HSI 8MHz 把串口跑起来 —— 之后无论时钟切换成功还是失败，我们都能开口说话 */
    uart_apply_baud(CPU_HZ_HSI);

    /* 对着主机的实际速率校准 BRR（HIS 偏差 1~3% 时，硬编码 BRR 会处于容差边缘） */
#if AUTOCAL && !BRR_FORCE
    uart_autocalibrate();
#endif

    uart_puts("LOG boot stage=1 hsi8mhz uart=ok\r\n");

#if USE_PLL
    if (clock_to_pll()) {
        s_cpu_hz = CPU_HZ_PLL;
        uart_apply_baud(CPU_HZ_PLL); /* 换主频必须重算 BRR，否则波特率全错 */
        uart_puts("LOG boot stage=2 pll72=ok\r\n");
    } else {
        s_cpu_hz = CPU_HZ_HSI;
        BOOT_MARKER = MARK_PLL_FAILED;
        uart_puts("LOG boot stage=2 pll72=FAILED (hse not ready) fallback=hsi8mhz\r\n");
        uart_puts("LOG hint: 板上 8MHz 晶振可能没焊/没起振，检查 X1 与负载电容\r\n");
    }
#else
    s_cpu_hz = CPU_HZ_HSI;
#if USE_HSE
    if (clock_to_hse()) {
        s_cpu_hz = CPU_HZ_HSE;
        uart_apply_baud(s_cpu_hz);
        uart_puts("LOG boot stage=2 hse8mhz=ok (crystal, brr recalced)\r\n");
    } else {
        BOOT_MARKER = MARK_PLL_FAILED;
        uart_puts("LOG boot stage=2 hse=FAILED fallback=hsi8mhz\r\n");
        uart_puts("LOG hint: 板上 8MHz 晶振没起振 —— 检查 X1 是否焊好、负载电容\r\n");
    }
#else
    uart_puts("LOG boot stage=2 clk=hsi8mhz (USE_HSE=0)\r\n");
#endif
#endif

    systick_init();

    /* 舵机 PWM 放在最后：PSC 依赖主频，必须等时钟最终确定（HSE/PLL 切换完）再配。
     * 一配置完就立刻输出**安全位**（Z 抬到最高、夹爪张开），
     * 避免上电瞬间爪子停在任意角度、甚至夹着货乱甩。 */
    servo_init();

    /* M3 步进脉冲发生器：只依赖 s_cpu_hz（PSC 要用主频算），也放在时钟定下来之后。
     * 初始化完**不启动**定时器、也不使能驱动器 —— 要显式 `EN 1` 电机才自锁。 */
    step_init();

    /* 串口屏：**默认 9600** —— 2026-09-23 实测本屏（TJC4832T135_011R）的出厂波特率就是 9600。
     * ⚠️ 波特率不对时屏**完全不响应**（它收到的是乱码、解析不了），是本项目最难查的一类问题：
     *    当时现象是"发什么都毫无变化 + 屏一个字节都不回"，而 MCU 侧全部正常（回环自检通过）。
     * 要改波特率：运行时 `LCDBAUD <n>`，或改这里的默认值。
     * 判据：发一条垃圾指令 `LCD zzz`，屏若回 `1A FF FF FF` 就说明**波特率是对的**。 */
    lcd_init(9600u);

    /* ⚠️ 2026-10-04：**不再使用无线桥 ESP32-C6** ✗（用户决定）⇒ USART2 让给串口屏 ✓
     *    所以这里**不再调用 u2_init()** —— 否则会把屏刚配好的 USART2 覆盖成 115200 ✗。
     *    u2_* 那套代码保留但**不初始化**（`U2*` 命令现在只回一句"已停用"，见 main.c）。 */

    /* 视觉板（CanMV/K210）：**唯一的第二命令通道**，115200 8N1，**PB10/PB11 = USART3**。
     * 同样放在最后：BRR 依赖 PCLK1，要等时钟定下来。 */
    u4_init(115200u);

#if UART_RX_IRQ
    NVIC_SetPriority(USART1_IRQn, 1);
    NVIC_EnableIRQ(USART1_IRQn);
    __enable_irq();
#endif
}
