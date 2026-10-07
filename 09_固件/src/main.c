/* M1 固件 —— 线协议解析 + 安全状态机
 *
 * M1 的目标：**把"通信层 + 安全门"钉死，但一个执行器都不碰**。
 * 因此这里所有运动/执行器命令都只做"该不该被允许"的判断，不做实际动作。
 * 判据（验收用）：
 *   PING        → OK pong
 *   乱码         → ERR 1 unknown（不装死）
 *   MOVE        → ERR 11 not homed（安全门真的生效）
 *   ESTOP/CLEAR → 状态真的变化
 *
 * 协议全文见 09_固件/固件设计-协议与引脚.md §3。
 */

#include "board.h"

#include "boot_marker.h"
#include "stm32f10x.h"

#define FW_VERSION "0.8.1"   /* 0.8.1：Z 端点按实测收紧（10°~170° ⇒ 630/2370µs，原 600/2400 各超 11µs） */
#define PROTO_VERSION "1"

#define LINE_MAX 160u /* 行长上限；超了回 ERR 10，避免缓冲区被灌爆。
                       * 2026-09-22 由 64 提到 160：串口屏指令（`LCD <内容>`）带中文
                       * 文本时 64 字节不够用（UTF-8 一个汉字 3 字节）。 */

/* --------------------------------------------------------------------------
 * 波特率测速模式（M1 临时诊断，测准后改 0）
 *
 * 背景：2026-09-21 实测——固件发出去的字节在 PC 侧全是乱码，而 bootloader 的字节
 * 完全正常。bootloader 不受影响是因为它**从 0x7F 自动测波特率**；固件则是按
 * "HSI = 8MHz"硬算 BRR。所以要么 HSI 不是 8MHz，要么 BRR 算错了。
 * 没有示波器、晶振丝印也看不清，于是用最朴素的办法测：
 *
 *   连发 500 行已知长度的文本 → 桥按行记录到达时间 → 首尾时间差 = 传输耗时
 *   → 真实波特率 = 总字节数 × 11bit ÷ 耗时   （8E1 = 1起始+8数据+1校验+1停止）
 *   → 真实主频 = 波特率 × BRR
 *
 * 500 行 × 18 字节 = 9000 字节，耗时约 1 秒 —— 用毫秒级时间戳测，误差 <0.2%。
 * ------------------------------------------------------------------------ */
/* 诊断模式：1 = 只做"测波特率"（`03_...` 未完，见日志），0 = 正常的 M1 固件。
 * 默认 0，让 build/m1.bin 就是 M1 本身；需要再排查时改成 1 即可。 */
#define BAUD_SELFTEST 0

/* 心跳诊断开关：1 = 启动后不进命令循环，改成每秒重复输出一行已知字符串。
 * 用来排查"串口乱码"时**摆脱对复位的依赖**（详见 main() 里的说明）。 */
#define HEARTBEAT 0

/* --------------------------------------------------------------------------
 * 中断开关（2026-09-22 线路 A 诊断后定案）：ALLOW_IRQ = 1 表示**不关中断**
 *
 * 背景：交接文档 §6 第 6 项记着"`systick_init()` 一开 TICKINT，`uart_puts` 就卡在
 * 等 TXE"，M1 靠下面的 `__disable_irq()` 绕开。但 **M3 的步进引擎必须用定时器
 * 中断**（20kHz 脉冲），绕不过去 → 现在把现象复现出来、定位它。
 *
 * ⚠️ 当前 `UART_RX_IRQ = 0`（见 board.c）→ USART1 的 NVIC 根本没开，
 *    所以打开中断后**唯一**会打断 CPU 的只有 SysTick。于是本实验的判读是：
 *      banner 四行完整 + `PING`→`OK pong`  ⇒ SysTick/TICKINT 安全，坑不在这里
 *      banner 完整     + PING 无回复        ⇒ 主循环能跑，不是中断风暴 → 另查
 *      banner 缺行／没有 + 无回复            ⇒ **中断风暴复现** → 读 RAM 脚印定位
 *
 * ✅ **结论（2026-09-22 实验 1 + 实验 2 实测）**：SysTick（TICKINT）与 USART1
 *    RX 中断路径**都正常**。
 *    当年的"坑"真凶是**向量表下标错位**（见 startup.c 第 89-93 行）：USART1 的向量
 *    被放到了 IRQn 21，真正的 IRQn 37 位置仍是 0 → **开中断 + RX 线上一个噪声起始位
 *    就跳到地址 0 跑飞**；卡死恰好落在"开中断后的第一次 uart_puts"，因此被误记成
 *    "SysTick 一开 TICKINT 就卡 TXE"。该错误已修 → 中断可放心用。
 *    → **保持 1**（M3 的定时器中断就靠它）；置 0 只用于"怀疑中断时快速排除"。
 * ------------------------------------------------------------------------ */
#define ALLOW_IRQ 1

/* --------------------------------------------------------------------------
 * 状态机（《设计》§4）。M1 可达的状态：
 *   INIT → IDLE_UNHOMED →(ESTOP)→ ESTOP →(CLEAR)→ IDLE_UNHOMED
 * HOMING/READY/MOVING 要等 M3 有了电机才有意义。
 * ------------------------------------------------------------------------ */
typedef enum {
    ST_INIT = 0,
    ST_IDLE_UNHOMED,
    ST_HOMING,
    ST_READY,
    ST_MOVING,
    ST_ESTOP,
    ST_FAULT,
} state_t;

static state_t s_state = ST_INIT;
static uint8_t s_homed = 0;         /* 位0=X 位1=Y；M1 恒为 0 */

/* ==========================================================================
 * M5 —— 「计划」收发：视觉板 K210（或赛前上位机）把**整批货的分拣计划**一次性交给 STM32
 *
 * 为什么要这么设计（对应规则红线"**运行期零外部通讯**"）：
 *   计划必须在**一键启动之前**整包给完；启动后 STM32 只执行，不再接受任何外部指令。
 *   所以协议做成"**声明总数 → 逐条上报 → 整包校验**"，中间任何一条不合法就整包作废。
 *
 * 报文（都带 `@` 前缀从视觉口进来，见 feed() 的过滤）：
 *   @PLAN <N>                      声明本次 N 条（1..24）
 *   @P <i> <x_mm> <y_mm> <box> <img> <name>  第 i 条（i 从 1 开始，必须顺序）
 *   @PLAN END                      收尾：条数对得上、且每条都合法 → 整包生效
 *   @PLAN?                         查询：ready/have/expect
 *   @PCLEAR                        清空（重来）
 *   @PPLAN                         打一份摘要（前若干条）
 *
 * 字段含义：
 *   x_mm / y_mm : 托盘坐标，**毫米**（有符号）—— 视觉侧算好，STM32 不懂像素
 *   box         : 1..6 号储物盒
 *   img         : 串口屏里那张货物图片的 ID（2..21）
 *   name        : 货物名称（最长 15 字符，给屏显示用；带中文时由屏侧按 GBK 处理）
 *
 * ⚠️ 校验口径：**整包要么全收、要么全废**（`PLAN END` 才生效）。
 *    单条里任何字段越界 → 那一条 `ERR`，并且**整包标记为坏**，不再接受 END。
 * ========================================================================== */
#define PLAN_MAX 24
#define PLAN_NAME_MAX 16

typedef struct {
    int16_t x_mm;
    int16_t y_mm;
    uint8_t box;      /* 1..6 */
    uint8_t img;      /* 屏内图片 ID */
    char    name[PLAN_NAME_MAX];
} plan_item_t;

static plan_item_t s_plan[PLAN_MAX];
static uint8_t s_plan_have;    /* 已收到的条数 */
static uint8_t s_plan_expect;  /* 声明要收几条 */
static uint8_t s_plan_ready;   /* 1 = 整包校验通过、可用于执行 */
static uint8_t s_plan_bad;     /* 1 = 本包已出错（END 会被拒） */

static uint32_t s_pos_x = 0;        /* 单位：步（M1 只是占位，恒为 0） */
static uint32_t s_pos_y = 0;
static uint16_t s_servo_z = 0;      /* 单位：µs 脉宽；M1 恒为 0 */
static uint16_t s_servo_grip = 0;
static uint8_t s_box_mask = 0;      /* 位0..5 = 入盒 1..6；M1 恒为 0（M2 接对射） */
static uint8_t s_z_inv = 0;         /* Z 轴百分比方向反转：1 = 0%↔100% 对调 */

/* ---- M2：入盒检测消抖参数（2026-09-22 按实测数据改，非阻塞采样）------------
 * 起因：原实现是"状态一变就隔 1ms 再采一次"，实测在 **1ms 内连跳三次**
 *   （840.349s box=000001 → 840.350s box=000000 → 840.350s box=000001）。
 * 这种抖动到 M4 会让 AI 把"一次移开"数成好几件货，而规则里"未全入盒不计数"
 * 代价很高，必须现在拍死。口径取《设计》§6：每 5ms 采一次、连续 4 次一致
 * 才认账（= 20ms）。
 * ⚠️ 刻意**不用阻塞忙等**：STM32 的 UART 只有 2 字节缓冲，阻塞 20ms 会丢掉
 *    上百个字节、把命令吃掉。这里用已在跑的 **TIM3（1MHz → 1 tick = 1µs）
 *    当时间基准**，采样间隔到了才看一眼，其余时间照常收串口；
 *    也刻意不碰 SysTick 中断（见交接文档 §6 第 6 项那个坑）。
 * ⚠️ TIM3 的 CNT 按 ARR 回绕（0~19999，周期 20ms），所以比较用
 *    "无符号减法"（(uint16_t)(now - last)），回绕时依然正确。
 * ------------------------------------------------------------------------ */
#define BOX_SAMPLE_MS 5u   /* 采样间隔（ms） */
#define BOX_STABLE_N  4u   /* 连续一致次数 → 5ms × 4 = 20ms */

static uint8_t  s_box_cand = 0;    /* 候选状态（还没被认账的读数） */
static uint8_t  s_box_cnt = 0;     /* 候选状态已连续一致的次数 */
static uint16_t s_box_last_us = 0; /* 上次采样时刻（TIM3->CNT 的 µs 计数） */

/* 百分比 → 脉宽。**只作用于 Z 轴的百分比命令**（ZPCT/ZUP/ZDOWN）；
 * ZSET 是"直接给脉宽"，永远不经过这里，否则标定时会被自己绕晕。
 * 注意映射到的是**工作行程** SERVO_Z_LO/HI（600~2400µs，两端留了余量），
 * 不是舵机的能力极限 500~2500µs —— 详见 board.h 里那段说明。 */
static uint16_t z_pct_to_us(uint32_t pct)
{
    uint16_t us = (uint16_t)(SERVO_Z_LO_US + (SERVO_Z_HI_US - SERVO_Z_LO_US) * pct / 100u);
    if (s_z_inv) {
        us = (uint16_t)(SERVO_Z_LO_US + SERVO_Z_HI_US - us);
    }
    return us;
}

/* --------------------------------------------------------------------------
 * 无 printf 的字符串拼装（M1 刻意不用 stdio：一旦引了 printf，标准库会把
 * 半主机（semihosting）拖进来，没有调试器时程序会卡死在 BKPT —— 这类问题
 * 极难查。报文都短，手拼最省心。）
 * ------------------------------------------------------------------------ */
static char *put_str(char *out, const char *text)
{
    while (*text != '\0') {
        *out++ = *text++;
    }
    return out;
}

static char *put_u32(char *out, uint32_t value)
{
    char tmp[11];
    int n = 0;
    if (value == 0) {
        *out++ = '0';
        return out;
    }
    while (value != 0 && n < 10) {
        tmp[n++] = (char)('0' + (value % 10u));
        value /= 10u;
    }
    while (n > 0) {
        *out++ = tmp[--n];
    }
    return out;
}

/* 带符号整数（M3b 的 POS 要用：位置可能是负的） */
static char *put_i32(char *out, int32_t value)
{
    if (value < 0) {
        *out++ = '-';
        return put_u32(out, (uint32_t)(-value));
    }
    return put_u32(out, (uint32_t)value);
}

static void reply(const char *text)
{
    uart_puts(text);
    uart_puts("\r\n");
}

static void send_ok(const char *payload)
{
    uart_puts("OK");
    if (payload != 0 && *payload != '\0') {
        uart_puts(" ");
        uart_puts(payload);
    }
    uart_puts("\r\n");
}

static void send_err(uint32_t code, const char *msg)
{
    char buf[80];
    char *p = buf;
    p = put_str(p, "ERR ");
    p = put_u32(p, code);
    p = put_str(p, " ");
    p = put_str(p, msg);
    *p = '\0';
    reply(buf);
}

static const char *state_name(state_t state)
{
    switch (state) {
    case ST_INIT:         return "INIT";
    case ST_IDLE_UNHOMED: return "IDLE_UNHOMED";
    case ST_HOMING:       return "HOMING";
    case ST_READY:        return "READY";
    case ST_MOVING:       return "MOVING";
    case ST_ESTOP:        return "ESTOP";
    case ST_FAULT:        return "FAULT";
    default:              return "?";
    }
}

static int streq(const char *a, const char *b)
{
    while (*a != '\0' && *a == *b) {
        a++;
        b++;
    }
    return *a == *b;
}

/* --------------------------------------------------------------------------
 * 安全门：所有运动/执行器命令都要先过这里。
 * 顺序很重要 —— 先判急停，再判回零：急停态下的报错必须是 ERR 14（更严重），
 * 否则操作者会以为是"忘了回零"，去回零，而设备其实还处在急停里。
 * ------------------------------------------------------------------------ */
static int motion_allowed(void)
{
    if (s_state == ST_ESTOP || s_state == ST_FAULT) {
        send_err(14, "estopped - send CLEAR first");
        return 0;
    }
    if (!s_homed) {
        send_err(11, "not homed - send ZERO x|y|all (or HOMED 1) first");
        return 0;
    }
    return 1;
}

/* --------------------------------------------------------------------------
 * 命令处理
 * ------------------------------------------------------------------------ */
#define MAX_TOKENS 8

static int parse_u32(const char *s, uint32_t *out)
{
    uint32_t v = 0;
    int digits = 0;

    while (*s >= '0' && *s <= '9') {
        v = v * 10u + (uint32_t)(*s - '0');
        s++;
        digits++;
    }
    if (digits == 0 || *s != '\0') {
        return 0; /* 空串或含非数字字符都算非法 */
    }
    *out = v;
    return 1;
}

/* M3 用：带符号整数（`STEP x -500` 这种）。允许前导 +/-，其余规则同 parse_u32。 */
static int parse_i32(const char *s, int32_t *out)
{
    uint32_t v = 0;
    int neg = 0;

    if (*s == '-') {
        neg = 1;
        s++;
    } else if (*s == '+') {
        s++;
    }
    if (!parse_u32(s, &v)) {
        return 0;
    }
    *out = neg ? -(int32_t)v : (int32_t)v;
    return 1;
}

/* 屏指令等"原样透传"命令的原始参数区。
 * ⚠️ 为什么需要它：下面的分词会把空格改写成 '\0'，而 `LCD page0.t0.txt="a b"` 这类
 *    屏指令**内部空格是有意义的**，所以必须在分词之前先抄一份出来。 */
static char s_arg_raw[LINE_MAX];

/* 诊断开关：USART2 收到什么就立刻原样吐回去（命令 `U2ECHO 1`）。
 * 用途：**不依赖上层解析**，直接验证"无线链路 ↔ USART2"这条电气通路通不通。
 * ⚠️ 开着时别接真实上位机（会把数据搅乱），验完记得 `U2ECHO 0`。 */
static int s_u2_echo = 0;

static int hex_val(char c)
{
    if (c >= '0' && c <= '9') { return c - '0'; }
    if (c >= 'a' && c <= 'f') { return c - 'a' + 10; }
    if (c >= 'A' && c <= 'F') { return c - 'A' + 10; }
    return -1;
}

static void handle_line(char *line)
{
    char *tok[MAX_TOKENS];
    int ntok = 0;

    /* 先抄"命令之后的原始字符串"（保留内部空格），供 LCD 这类透传命令用 */
    {
        const char *sp = line;
        uint32_t i = 0;

        while (*sp != '\0' && *sp != ' ' && *sp != '\t') { sp++; }
        while (*sp == ' ' || *sp == '\t') { sp++; }
        while (sp[i] != '\0' && i + 1u < LINE_MAX) { s_arg_raw[i] = sp[i]; i++; }
        s_arg_raw[i] = '\0';
    }

    /* 分词：空格/制表符分隔。'?' 是普通字符（BOX? 是个完整命令名） */
    char *p = line;
    while (*p != '\0' && ntok < MAX_TOKENS) {
        while (*p == ' ' || *p == '\t') {
            *p++ = '\0';
        }
        if (*p == '\0') {
            break;
        }
        tok[ntok++] = p;
        while (*p != '\0' && *p != ' ' && *p != '\t') {
            p++;
        }
    }
    if (ntok == 0) {
        return; /* 空行忽略（CRLF 的 \r 之后会留下一个空行） */
    }

    const char *cmd = tok[0];

    /* ---- 永远可用的命令（急停态下也必须能应答） ---- */
    if (streq(cmd, "PING")) {
        send_ok("pong");
        return;
    }
    if (streq(cmd, "VER")) {
        send_ok("fw=" FW_VERSION " proto=" PROTO_VERSION);
        return;
    }
    if (streq(cmd, "HELP")) {
        send_ok("commands=PING,VER,HELP,STATUS,GETPOS,BOX?,MS?,SAFE,ESTOP,CLEAR,RESET,"
                "ZUP,ZDOWN,ZSET,ZPCT,ZINV,GRIP,RELEASE,GSET,GPCT,"
                "EN,SPD,STEP,POS,STOP,M3?,"
                "ACC,SMM,MOVE,ZERO,HOMED,LIM,"
                "V4,V4?,"
                "PLAN,PI,PCLEAR,PLAN?,PLAND,"
                "LCD,LCDRAW,LCDBAUD,LCDRD,USART3?,LCDPD,"
                "U2,U2RAW,U2?,U2PD,U2ECHO");
        return;
    }
    if (streq(cmd, "STATUS")) {
        char buf[96];
        char *q = buf;
        q = put_str(q, "state=");
        q = put_str(q, state_name(s_state));
        q = put_str(q, " homed=");
        q = put_u32(q, (uint32_t)s_homed);
        q = put_str(q, " estop=");
        q = put_u32(q, (s_state == ST_ESTOP || s_state == ST_FAULT) ? 1u : 0u);
        q = put_str(q, " box=");
        /* 6 位二进制，位0=盒1 */
        for (int i = 5; i >= 0; i--) {
            *q++ = (char)('0' + ((s_box_mask >> i) & 1u));
        }
        q = put_str(q, " pos=");
        q = put_u32(q, s_pos_x);
        *q++ = ',';
        q = put_u32(q, s_pos_y);
        *q++ = ',';
        q = put_u32(q, (uint32_t)s_servo_z);
        *q++ = ',';
        q = put_u32(q, (uint32_t)s_servo_grip);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "GETPOS")) {
        char buf[64];
        char *q = buf;
        q = put_str(q, "x=");
        q = put_u32(q, s_pos_x);
        q = put_str(q, " y=");
        q = put_u32(q, s_pos_y);
        q = put_str(q, " z=");
        q = put_u32(q, (uint32_t)s_servo_z);
        q = put_str(q, " grip=");
        q = put_u32(q, (uint32_t)s_servo_grip);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "MS?")) {
        /* 自证 SysTick 真的在走：连发两次，两次差值应约等于间隔的毫秒数。
         * M3 之前必须确认"中断真的在触发"，而不是"使能了但没进去"。 */
        char buf[24];
        char *q = buf;
        q = put_str(q, "ms=");
        q = put_u32(q, board_millis());
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "BOX?")) {
        char buf[16];
        char *q = buf;
        q = put_str(q, "box=");
        for (int i = 5; i >= 0; i--) {
            *q++ = (char)('0' + ((s_box_mask >> i) & 1u));
        }
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "ESTOP")) {
        s_state = ST_ESTOP;
        /* ⚠️ 刻意**不**动舵机：急停的语义是"立刻停住"，此刻把 Z 抬起来或张开爪子
         * 反而是在制造动作。要回安全位请显式发 SAFE（或 CLEAR）。
         * 这与设计文档里"急停时舵机回安全位"的旧写法不同 —— 已按"急停=冻结"实现，
         * 若坚持旧语义请说一声，一行就能改回来。 */
        send_ok("estop");
        reply("EVT ESTOP reason=cmd"); /* 先应答再报事件：应答必须在 100ms 内 */
        return;
    }
    if (streq(cmd, "CLEAR")) {
        /* 急停可能发生在任意位置，位置可信度必须清零 → 强制回到"未回零" */
        s_homed = 0;
        s_state = ST_IDLE_UNHOMED;
        send_ok("cleared");
        /* 解除急停后回安全位：Z 抬到最高、夹爪张开（此时再动是安全的） */
        servo_set_z(SERVO_SAFE_Z_US);
        s_servo_z = SERVO_SAFE_Z_US;
        servo_set_grip(SERVO_SAFE_GRIP_US);
        s_servo_grip = SERVO_SAFE_GRIP_US;
        return;
    }
    if (streq(cmd, "RESET")) {
        send_ok("reset");
        /* 等一下让报文发完再复位（uart_puts 已经等 TC，这里只是保险） */
        for (volatile uint32_t i = 0; i < 200000u; i++) { }
        NVIC_SystemReset();
        return;
    }

    /* ---- 运动类：M3b 已经实现（见后面的 MOVE/STEP 块）。
     * ⚠️ 2026-10-03 踩坑：这里原来留着 M1 的桩 —— `MOVE/JOG` 走到这里就 `send_err(18,...)` 返回了，
     *    而它前面还有 `motion_allowed()`，里面 `s_homed` 是**写死 0** 的（M1 注释："恒为 0"）→
     *    **所有 MOVE 都回 `ERR 11 not homed`，根本到不了 M3b 的实现** ✗。
     *    现在把桩删掉；"未回零"这道门改由 `s_homed` 表达，并在下面几处显式置位：
     *      · `ZERO x|y|all` —— 手动把当前位置当零点（没装限位开关时就这么用）✓ 置位
     *      · `HOMED 1`      —— 明确告诉固件"我知道现在在哪了" ✓ 置位
     *      · `CLEAR`/急停复位时清零（见 RESET/ESTOP 处理）✗ */
    if (streq(cmd, "HOME")) {
        if (s_state == ST_ESTOP || s_state == ST_FAULT) {
            send_err(14, "estopped - send CLEAR first");
            return;
        }
        send_err(18, "homing needs limit switches (not installed yet) - use ZERO instead");
        return;
    }
    /* ---- M2：舵机（Z 轴升降 + 单舵机夹爪）--------------------------------
     * 不带参数的命令走两个标定位；带参数的用来**现场标定**：
     *   ZSET/GSET <µs>     直接给脉宽（500~2500）—— 标定时最直观
     *   ZPCT/GPCT <0~100>  给行程百分比 —— 换机构时不用改协议
     *
     * ⚠️ 舵机**没有位置反馈**：下面的 `EVT *_DONE` 只表示"命令已下发"，
     *    不等于"真的到位了"。真正的到位确认要靠时间 / 微动开关（M2 还没接微动）。
     * ⚠️ 急停态下一律拒绝动作，必须先 CLEAR。 */
    if (streq(cmd, "ZUP") || streq(cmd, "ZDOWN") || streq(cmd, "ZSET") ||
        streq(cmd, "ZPCT") || streq(cmd, "GRIP") || streq(cmd, "RELEASE") ||
        streq(cmd, "GSET") || streq(cmd, "GPCT")) {
        if (s_state == ST_ESTOP || s_state == ST_FAULT) {
            send_err(14, "estopped - send CLEAR first");
            return;
        }
        if (streq(cmd, "ZUP")) {
            uint16_t us = s_z_inv ? SERVO_Z_LO_US : SERVO_Z_HI_US;
            servo_set_z(us);
            s_servo_z = us;
            send_ok("zup");
            reply("EVT Z_DONE UP");
            return;
        }
        if (streq(cmd, "ZDOWN")) {
            uint16_t us = s_z_inv ? SERVO_Z_HI_US : SERVO_Z_LO_US;
            servo_set_z(us);
            s_servo_z = us;
            send_ok("zdown");
            reply("EVT Z_DONE DOWN");
            return;
        }
        if (streq(cmd, "GRIP")) {
            servo_set_grip(SERVO_GRIP_CLOSED_US);
            s_servo_grip = SERVO_GRIP_CLOSED_US;
            send_ok("grip");
            /* 微动开关还没接 → loaded 恒报 0（"未确认"），不假装知道夹住了没有 */
            reply("EVT GRIP_DONE loaded=0");
            return;
        }
        if (streq(cmd, "RELEASE")) {
            servo_set_grip(SERVO_MAX_US);
            s_servo_grip = SERVO_MAX_US;
            send_ok("release");
            reply("EVT RELEASE_DONE");
            return;
        }

        /* 剩下四个都带一个数值参数 */
        {
            uint32_t v = 0;
            if (ntok < 2 || !parse_u32(tok[1], &v)) {
                send_err(10, "need numeric value");
                return;
            }
            uint16_t us;
            if (streq(cmd, "ZSET") || streq(cmd, "GSET")) {
                if (v < SERVO_MIN_US || v > SERVO_MAX_US) {
                    send_err(10, "us out of 500..2500");
                    return;
                }
                us = (uint16_t)v;
            } else {
                if (v > 100u) {
                    send_err(10, "pct out of 0..100");
                    return;
                }
                /* Z 轴百分比走方向反转；夹爪不反转（张开/闭合的语义是固定的） */
                us = (cmd[0] == 'Z') ? z_pct_to_us(v)
                                     : (uint16_t)(SERVO_MIN_US + (SERVO_MAX_US - SERVO_MIN_US) * v / 100u);
            }
            if (cmd[0] == 'Z') {
                servo_set_z(us);
                s_servo_z = us;
            } else {
                servo_set_grip(us);
                s_servo_grip = us;
            }
            send_ok("set");
            return;
        }
    }
    /* ------------------------------------------------------------------
     * M3 —— X/Y 步进（软件脉冲发生器，设计见 board.h 的 M3 段）
     * 典型用法：`EN 1` → `SPD 800` → `STEP x 800`（800 步 ≈ 1/4 圈 @3200步/圈）
     * ------------------------------------------------------------------ */
    if (streq(cmd, "EN")) {
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 1u) {
            send_err(10, "need EN 0|1");
            return;
        }
        step_enable((int)v);
        reply(v ? "OK en=1 motors locked" : "OK en=0 motors free");
        return;
    }
    if (streq(cmd, "SPD")) {
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v < 10u || v > STEP_MAX_HZ) {
            send_err(10, "SPD 10..5000 Hz");
            return;
        }
        step_set_rate(v);
        send_ok("spd");
        return;
    }
    if (streq(cmd, "STEP") || streq(cmd, "JOG")) {
        int axis;
        int32_t steps = 0;
        uint32_t hz;
        int rc;

        if (!motion_allowed()) {           /* 急停(ERR14) 优先于 未对零(ERR11) */
            return;
        }
        if (ntok < 3) {
            send_err(10, "need STEP x|y <steps> [hz]");
            return;
        }
        if (streq(tok[1], "x") || streq(tok[1], "X")) {
            axis = STEP_AXIS_X;
        } else if (streq(tok[1], "y") || streq(tok[1], "Y")) {
            axis = STEP_AXIS_Y;
        } else {
            send_err(10, "axis must be x or y");
            return;
        }
        if (!parse_i32(tok[2], &steps)) {
            send_err(10, "steps must be an integer (may be negative)");
            return;
        }
        hz = step_rate_hz();
        if (ntok >= 4 && !parse_u32(tok[3], &hz)) {
            send_err(10, "hz must be a number");
            return;
        }
        rc = step_jog(axis, steps, hz);
        if (rc != 0) {
            /* 把"为什么不动"直接说清楚，别让人对着电机猜 */
            if (rc == -3) {
                send_err(15, "axis busy - wait or STOP");
            } else if (rc == -4) {
                send_err(16, "not enabled - send EN 1 first");
            } else if (rc == -2) {
                send_err(10, "steps is zero");
            } else {
                send_err(10, "bad axis");
            }
            return;
        }
        reply("OK jog started");
        return;
    }
    if (streq(cmd, "STOP")) {
        step_stop();
        reply("OK stopped");
        return;
    }
    if (streq(cmd, "POS")) {
        /* 位置同时给"步"和"毫米"（毫米只是步数 ÷ SMM，没有反馈，纯换算） */
        char buf[160];
        char *q = buf;
        int32_t px = step_pos(STEP_AXIS_X);
        int32_t py = step_pos(STEP_AXIS_Y);
        int32_t smm = (int32_t)step_smm();
        q = put_str(q, "x=");
        q = put_i32(q, px);
        q = put_str(q, "mm=");
        q = put_i32(q, px / smm);
        q = put_str(q, " y=");
        q = put_i32(q, py);
        q = put_str(q, "mm=");
        q = put_i32(q, py / smm);
        q = put_str(q, " busy=");
        q = put_u32(q, (uint32_t)step_busy());
        q = put_str(q, " smm=");
        q = put_u32(q, step_smm());
        *q = '\0';                       /* ⚠️ 必须补：否则 send_ok 会把未初始化的栈内存一起打出去 */
        send_ok(buf);
        return;
    }
    if (streq(cmd, "M3?")) {
        /* 步进不动时的第一站：这里能看到"定时器在不在跑 / 速率 / 引脚配置" */
        char buf[160];
        char *q = buf;
        q = put_str(q, "isr=");
        q = put_u32(q, (uint32_t)STEP_ISR_HZ);
        q = put_str(q, " max=");
        q = put_u32(q, (uint32_t)STEP_MAX_HZ);
        q = put_str(q, " spd=");
        q = put_u32(q, step_rate_hz());
        q = put_str(q, " busy=");
        q = put_u32(q, (uint32_t)step_busy());
        q = put_str(q, " tim4cr1=");
        q = put_u32(q, (uint32_t)TIM4->CR1);
        q = put_str(q, " aodr=");
        q = put_u32(q, (uint32_t)GPIOA->ODR);
        q = put_str(q, " acrl=");
        q = put_u32(q, (uint32_t)GPIOA->CRL);
        q = put_str(q, " smm=");
        q = put_u32(q, step_smm());
        q = put_str(q, " ramp=");
        q = put_u32(q, step_ramp());
        q = put_str(q, " lim=");
        q = put_u32(q, (uint32_t)(step_limit_on(STEP_AXIS_X) || step_limit_on(STEP_AXIS_Y)));
        q = put_str(q, " ticks=");
        q = put_u32(q, step_ticks());
        q = put_str(q, " rate=");
        q = put_u32(q, step_rate_now(STEP_AXIS_X));
        q = put_str(q, " half=");
        q = put_u32(q, step_half_now(STEP_AXIS_X));
        *q = '\0';                       /* ⚠️ 同 POS：漏了这句就会打印栈里的垃圾 */
        send_ok(buf);
        return;
    }
    /* ---- M3b：加减速 / 毫米 / 软限位 ---- */
    if (streq(cmd, "ACC")) {
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 100000u) {
            send_err(10, "ACC 0..100000 (steps to reach speed; 0 = no ramp)");
            return;
        }
        step_set_ramp(v);
        reply(v ? "OK acc" : "OK acc=0 no-ramp");
        return;
    }
    if (streq(cmd, "SMM")) {
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v < 1u || v > 100000u) {
            send_err(10, "SMM 1..100000 (steps per mm)");
            return;
        }
        step_set_smm(v);
        send_ok("smm");
        return;
    }
    if (streq(cmd, "MOVE")) {
        /* MOVE <x|y> <±mm> [hz] —— 走多少毫米（依赖 SMM 换算） */
        int axis;
        int32_t mm = 0;
        uint32_t hz;
        int rc;

        if (!motion_allowed()) {
            return;
        }
        if (ntok < 3) {
            send_err(10, "need MOVE x|y <mm> [hz]");
            return;
        }
        if (streq(tok[1], "x") || streq(tok[1], "X")) {
            axis = STEP_AXIS_X;
        } else if (streq(tok[1], "y") || streq(tok[1], "Y")) {
            axis = STEP_AXIS_Y;
        } else {
            send_err(10, "axis must be x or y");
            return;
        }
        if (!parse_i32(tok[2], &mm)) {
            send_err(10, "mm must be an integer (may be negative)");
            return;
        }
        hz = step_rate_hz();
        if (ntok >= 4 && !parse_u32(tok[3], &hz)) {
            send_err(10, "hz must be a number");
            return;
        }
        rc = step_move_mm(axis, mm, hz);
        if (rc == -5) {
            send_err(17, "soft limit hit - ZERO or LIM off first");
        } else if (rc == -3) {
            send_err(15, "axis busy - wait or STOP");
        } else if (rc == -4) {
            send_err(16, "not enabled - send EN 1 first");
        } else if (rc != 0) {
            send_err(10, "move refused");
        } else {
            reply("OK move started");
        }
        return;
    }
    if (streq(cmd, "ZERO")) {
        /* ZERO x|y|all —— 把当前位置记为 0（**没有回零开关时的手动零点**） */
        if (ntok < 2) {
            send_err(10, "need ZERO x|y|all");
            return;
        }
        if (streq(tok[1], "x") || streq(tok[1], "X")) {
            step_zero(STEP_AXIS_X);
            s_homed |= 1u;                    /* 手动对零 = 这个轴"知道自己在哪了" */
        } else if (streq(tok[1], "y") || streq(tok[1], "Y")) {
            step_zero(STEP_AXIS_Y);
            s_homed |= 2u;
        } else if (streq(tok[1], "all")) {
            step_zero(-1);
            s_homed = 3u;
        } else {
            send_err(10, "need ZERO x|y|all");
            return;
        }
        send_ok("zero");
        return;
    }
    if (streq(cmd, "HOMED")) {
        /* 显式声明"我知道现在在哪了"（装了限位开关、或用 ZERO 手动对零之后都可以这么用）。
         * bit0 = X、bit1 = Y；0 = 全部重新变成"未定位"（运动命令会被安全门挡住）。 */
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 3u) {
            send_err(10, "need HOMED 0..3 (bit0=X bit1=Y)");
            return;
        }
        s_homed = (uint8_t)v;
        send_ok("homed");
        return;
    }
    if (streq(cmd, "LIM")) {
        /* LIM <x|y> <min_mm> <max_mm> ／ LIM <x|y> off ／ LIM off */
        int axis;
        if (ntok < 2) {
            send_err(10, "need LIM x|y <min_mm> <max_mm> | off");
            return;
        }
        if (streq(tok[1], "off")) {
            step_clear_limit(-1);
            send_ok("lim off");
            return;
        }
        if (streq(tok[1], "x") || streq(tok[1], "X")) {
            axis = STEP_AXIS_X;
        } else if (streq(tok[1], "y") || streq(tok[1], "Y")) {
            axis = STEP_AXIS_Y;
        } else {
            send_err(10, "axis must be x or y");
            return;
        }
        if (ntok >= 3 && streq(tok[2], "off")) {
            step_clear_limit(axis);
            send_ok("lim off");
            return;
        }
        {
            int32_t lo = 0, hi = 0;
            if (ntok < 4 || !parse_i32(tok[2], &lo) || !parse_i32(tok[3], &hi)) {
                send_err(10, "need LIM x|y <min_mm> <max_mm>");
                return;
            }
            step_set_limit(axis, lo, hi);
            send_ok("lim set");
            return;
        }
    }
    /* ------------------------------------------------------------------
     * M5 —— 计划收发（视觉板 K210 → STM32）
     * 报文说明见文件上方的 PLAN 段注释。
     * ------------------------------------------------------------------ */
    if (streq(cmd, "PLAN")) {
        if (s_state == ST_MOVING) {
            send_err(19, "busy - plan locked while moving");
            return;
        }
        if (ntok < 2) {
            send_err(10, "need PLAN <N> or PLAN END");
            return;
        }
        if (streq(tok[1], "END")) {
            if (s_plan_bad) {
                send_err(12, "plan has bad rows - resend whole plan");
                return;
            }
            if (s_plan_have == 0u || s_plan_have != s_plan_expect) {
                send_err(12, "plan incomplete");
                return;
            }
            s_plan_ready = 1u;
            reply("OK plan loaded");
            return;
        }
        {
            uint32_t n = 0;
            if (!parse_u32(tok[1], &n) || n < 1u || n > (uint32_t)PLAN_MAX) {
                send_err(10, "PLAN 1..24");
                return;
            }
            s_plan_expect = (uint8_t)n;
            s_plan_have = 0u;
            s_plan_ready = 0u;
            s_plan_bad = 0u;
            send_ok("plan begin");
            return;
        }
    }
    if (streq(cmd, "PI")) {
        /* PI <i> <x_mm> <y_mm> <box> <img> <name> —— 第 i 条（必须顺序、字段越界即整包作废） */
        uint32_t i, box, img, k;
        int32_t x, y;
        plan_item_t *it;

        if (s_plan_expect == 0u) {
            send_err(12, "send PLAN <N> first");
            return;
        }
        if (s_plan_bad) {
            send_err(12, "plan already bad - resend whole plan");
            return;
        }
        if (ntok < 7) {
            send_err(10, "PI <i> <x> <y> <box> <img> <name>");
            return;
        }
        if (!parse_u32(tok[1], &i) || i != (uint32_t)s_plan_have + 1u
            || i > (uint32_t)s_plan_expect) {
            s_plan_bad = 1u;                 /* 序号乱/超量 = 整包废掉，避免"错位对号入座" */
            send_err(12, "row index out of order");
            return;
        }
        if (!parse_i32(tok[2], &x) || !parse_i32(tok[3], &y)) {
            s_plan_bad = 1u;
            send_err(10, "x/y must be integer mm");
            return;
        }
        if (x < -1000 || x > 1000 || y < -1000 || y > 1000) {
            s_plan_bad = 1u;
            send_err(10, "x/y out of -1000..1000mm");
            return;
        }
        if (!parse_u32(tok[4], &box) || box < 1u || box > 6u) {
            s_plan_bad = 1u;
            send_err(10, "box 1..6");
            return;
        }
        if (!parse_u32(tok[5], &img) || img < 2u || img > 21u) {
            s_plan_bad = 1u;
            send_err(10, "img 2..21");
            return;
        }
        it = &s_plan[s_plan_have];
        it->x_mm = (int16_t)x;
        it->y_mm = (int16_t)y;
        it->box = (uint8_t)box;
        it->img = (uint8_t)img;
        for (k = 0; k + 1u < (uint32_t)PLAN_NAME_MAX && tok[6][k] != '\0'; k++) {
            it->name[k] = tok[6][k];
        }
        it->name[k] = '\0';
        s_plan_have++;
        send_ok("pi");
        return;
    }
    if (streq(cmd, "PCLEAR")) {
        s_plan_have = 0u;
        s_plan_expect = 0u;
        s_plan_ready = 0u;
        s_plan_bad = 0u;
        send_ok("plan cleared");
        return;
    }
    if (streq(cmd, "PLAN?")) {
        char buf[96];
        char *q = buf;
        q = put_str(q, "ready=");
        q = put_u32(q, (uint32_t)s_plan_ready);
        q = put_str(q, " have=");
        q = put_u32(q, (uint32_t)s_plan_have);
        q = put_str(q, " expect=");
        q = put_u32(q, (uint32_t)s_plan_expect);
        q = put_str(q, " bad=");
        q = put_u32(q, (uint32_t)s_plan_bad);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "PLAND")) {
        /* 逐条打一遍（核对用；24 条最多 24 行） */
        uint32_t i;
        for (i = 0u; i < (uint32_t)s_plan_have; i++) {
            char buf[96];
            char *q = buf;
            q = put_str(q, "pi i=");
            q = put_u32(q, i + 1u);
            q = put_str(q, " x=");
            q = put_i32(q, s_plan[i].x_mm);
            q = put_str(q, " y=");
            q = put_i32(q, s_plan[i].y_mm);
            q = put_str(q, " box=");
            q = put_u32(q, s_plan[i].box);
            q = put_str(q, " img=");
            q = put_u32(q, s_plan[i].img);
            q = put_str(q, " name=");
            q = put_str(q, s_plan[i].name);
            *q = '\0';
            send_ok(buf);
        }
        send_ok("pland end");
        return;
    }
    if (streq(cmd, "SAFE")) {
        servo_set_z(SERVO_SAFE_Z_US);
        s_servo_z = SERVO_SAFE_Z_US;
        servo_set_grip(SERVO_SAFE_GRIP_US);
        s_servo_grip = SERVO_SAFE_GRIP_US;
        send_ok("safe z=up grip=open");
        return;
    }
    if (streq(cmd, "TIM3?")) {
        /* 调试用：把 TIM3 的配置原样报出来。
         * 舵机不动时先看这里 —— 如果 psc/arr/ccr 都对、ccer 的 CC3E 也置了，
         * 那固件这一侧就是干净的，问题只在引脚/线/供电（或 TIM3 重映射）。
         * cnt 每次读都在变，说明计数器确实在跑。 */
        char buf[160];
        char *q = buf;
        q = put_str(q, "psc=");
        q = put_u32(q, (uint32_t)TIM3->PSC);
        q = put_str(q, " arr=");
        q = put_u32(q, (uint32_t)TIM3->ARR);
        q = put_str(q, " ccr3=");
        q = put_u32(q, (uint32_t)TIM3->CCR3);
        q = put_str(q, " ccr4=");
        q = put_u32(q, (uint32_t)TIM3->CCR4);
        q = put_str(q, " cr1=");
        q = put_u32(q, (uint32_t)TIM3->CR1);
        q = put_str(q, " ccer=");
        q = put_u32(q, (uint32_t)TIM3->CCER);
        q = put_str(q, " cnt=");
        q = put_u32(q, (uint32_t)TIM3->CNT);
        q = put_str(q, " bcrl=");
        q = put_u32(q, (uint32_t)GPIOB->CRL);
        q = put_str(q, " mapr=");
        q = put_u32(q, (uint32_t)AFIO->MAPR);
        q = put_str(q, " zinv=");
        q = put_u32(q, (uint32_t)s_z_inv);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "ZINV")) {
        /* Z 轴百分比方向反转：1 = 0%↔100% 对调。
         * 为什么做成运行时命令：机构装配方向是现场才知道的，改这个不该要重新烧固件。
         * 注意它**不改变 ZSET**（直接给脉宽永远按原值走），所以标定时不会被自己绕晕。 */
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 1u) {
            send_err(10, "ZINV 0|1");
            return;
        }
        s_z_inv = (uint8_t)v;
        send_ok(s_z_inv ? "z_inv=1 (0%<->100% 已对调)" : "z_inv=0 (正向)");
        return;
    }
    if (streq(cmd, "LCDPD")) {
        /* 诊断：把 PB11 切到"下拉输入"，用来客观判断**屏的 TX 到底有没有接着**。
         *   LCDPD 1 → 下拉  → 再看 USART3? 的 idr：bit11 仍为 1 = 屏在驱动这条线（接上了）
         *                                        bit11 = 0 = 那根线没通
         *   LCDPD 0 → 恢复浮空（正常接收必须恢复！） */
        uint32_t v = 0;
        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 1u) {
            send_err(10, "LCDPD 0|1");
            return;
        }
        lcd_pulldown((int)v);
        send_ok(v ? "pb11=pull_down (看完记得 LCDPD 0)" : "pb11=floating");
        return;
    }
    if (streq(cmd, "USART3?")) {
        /* 屏不动时的**第一站**：把 USART3 与 PB10/PB11 的真实状态报出来。
         * 判读要点（2026-09-22 加，回环自检时就是靠它定案的）：
         *   idr 的 bit10(PB10) / bit11(PB11) = **引脚的实际电平**
         *     · 空闲时 PB10 必须是 1（UART 的 TX 空闲是高电平）
         *       → 若 bit10=0，说明 PB10 根本没被 USART3 驱动（引脚配置/AF 不对）
         *     · 回环（PB10 短接 PB11）时 PB11 应跟着 PB10 走
         *       → 若 bit10=1 而 bit11=0，说明**那根线没真正接上**，或 PB11 被别处拉低
         *   crh = PB10/PB11 的配置：PB10 应为 **0xB**（复用推挽 50MHz）、
         *         PB11 应为 **0x4**（浮空输入）→ 即整个 CRH 的 bit[15:8] = 0x4B
         *   sr 里若 FE/ORE/PE 常置位，说明电平/波特率有问题 */
        char buf[160];
        char *q = buf;
        q = put_str(q, "cr1=");
        q = put_u32(q, (uint32_t)USART3->CR1);
        q = put_str(q, " brr=");
        q = put_u32(q, (uint32_t)USART3->BRR);
        q = put_str(q, " sr=");
        q = put_u32(q, (uint32_t)USART3->SR);
        q = put_str(q, " crh=");
        q = put_u32(q, (uint32_t)GPIOB->CRH);
        q = put_str(q, " idr=");
        q = put_u32(q, (uint32_t)GPIOB->IDR);
        q = put_str(q, " lcdbaud=");
        q = put_u32(q, lcd_baud());
        *q = '\0';
        send_ok(buf);
        return;
    }
    /* ---- M4：无线桥串口（USART2 ↔ ESP32-C6）-----------------------------
     * 用途：让"从电脑经无线链路"过来的命令也能被本固件执行，并且**回复原路返回**
     *      —— 这样 PC 从无线链路发 `PING` 就能收到 `OK pong`，证明信息真的到了 STM32。
     *   U2 <文本>     往 USART2 发一段文本（自动补 CRLF）
     *   U2RAW <hex>   往 USART2 发原始字节（不补）
     *   U2?           回读收发计数 + CR1 —— 判"到底有没有收到字节"的客观依据
     *   U2PD 1|0      诊断：PA3 切下拉，判 C6 的 TX 有没有接到 PA3（用完记得 U2PD 0） */
    if (streq(cmd, "V4")) {
        /* V4 <text> —— 主动往**视觉板 K210**（UART4）发一行 */
        char buf[48];
        char *q = buf;

        if (s_arg_raw[0] == '\0') {
            send_err(22, "v4 needs text");
            return;
        }
        u4_send_text(s_arg_raw);
        u4_send_text("\r\n");
        q = put_str(q, "v4_sent=");
        q = put_u32(q, u4_tx_count());
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "V4?")) {
        /* 视觉口诊断：收了/发了多少字节、CR1、BRR（判"K210 那条线到底通没通"） */
        char buf[96];
        char *q = buf;
        q = put_str(q, "v4_rx=");
        q = put_u32(q, u4_rx_count());
        q = put_str(q, " v4_tx=");
        q = put_u32(q, u4_tx_count());
        q = put_str(q, " cr1=");
        q = put_u32(q, (uint32_t)USART3->CR1);
        q = put_str(q, " brr=");
        q = put_u32(q, (uint32_t)USART3->BRR);
        q = put_str(q, " baud=115200");
        *q = '\0';
        send_ok(buf);
        return;
    }
    /* ⚠️ 2026-10-04：**无线桥（ESP32-C6）已停用** ✗ —— USART2 现在归串口屏 ✓，
     *    所以所有 `U2*` 命令统一回一句明确的"已停用"，不再真的往 USART2 写东西
     *    （否则会把命令字节灌进屏里 ✗）。要恢复无线链路：board.c 里加回 u2_init()，
     *    并把串口屏挪到别的口（C8T6 上就得回到软件串口了）。 */
    if (cmd[0] == 'U' && cmd[1] == '2') {
        send_err(21, "wireless bridge (U2) disabled - USART2 is the lcd now");
        return;
    }
    if (streq(cmd, "U2")) {
        char buf[48];
        char *q = buf;

        if (s_arg_raw[0] == '\0') {
            send_err(22, "u2 needs text");
            return;
        }
        u2_send_text(s_arg_raw);
        u2_send_text("\r\n"); /* 补换行，方便对端按行看 */
        q = put_str(q, "u2_sent=");
        q = put_u32(q, u2_tx_count());
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "U2RAW")) {
        uint8_t raw[LINE_MAX / 2u];
        uint32_t n = 0;
        const char *h = s_arg_raw;

        while (h[0] != '\0' && h[1] != '\0' && n < (uint32_t)sizeof(raw)) {
            int hi = hex_val(h[0]);
            int lo = hex_val(h[1]);

            if (hi < 0 || lo < 0) {
                send_err(20, "bad hex");
                return;
            }
            raw[n++] = (uint8_t)((hi << 4) | lo);
            h += 2;
        }
        if (n == 0u) {
            send_err(20, "bad hex");
            return;
        }
        u2_send_bytes(raw, n);
        {
            char buf[32];
            char *q = buf;
            q = put_str(q, "u2_raw=");
            q = put_u32(q, n);
            *q = '\0';
            send_ok(buf);
        }
        return;
    }
    if (streq(cmd, "U2?")) {
        char buf[96];
        char *q = buf;
        static const char *hexd = "0123456789ABCDEF";
        uint32_t cr1 = USART2->CR1;
        int i;

        q = put_str(q, "u2_rx=");
        q = put_u32(q, u2_rx_count());
        q = put_str(q, " u2_tx=");
        q = put_u32(q, u2_tx_count());
        q = put_str(q, " cr1=0x");
        for (i = 28; i >= 0; i -= 4) { /* UE|TE|RE|RXNEIE 都应该置上 */
            *q++ = hexd[(cr1 >> i) & 0xFu];
        }
        q = put_str(q, " brr=");
        q = put_u32(q, (uint32_t)USART2->BRR); /* 8MHz/115200 → 应为 69 */
        q = put_str(q, " baud=");
        q = put_u32(q, 115200u);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "U2PD")) {
        uint32_t v;
        char buf[48];
        char *q = buf;

        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 1u) {
            send_err(22, "u2pd 0|1");
            return;
        }
        u2_pulldown((int)v);
        q = put_str(q, "pa3_pd=");
        q = put_u32(q, v);
        q = put_str(q, " pa3_idr=");
        q = put_u32(q, (GPIOA->IDR >> 3) & 1u);
        *q = '\0';
        send_ok(buf);
        return;
    }
    if (streq(cmd, "U2ECHO")) {
        uint32_t v;
        char buf[32];
        char *q = buf;

        if (ntok < 2 || !parse_u32(tok[1], &v) || v > 1u) {
            send_err(22, "u2echo 0|1");
            return;
        }
        s_u2_echo = (int)v;
        q = put_str(q, "u2_echo=");
        q = put_u32(q, (uint32_t)s_u2_echo);
        *q = '\0';
        send_ok(buf);
        return;
    }

    /* ---- M2+：串口屏（淘晶驰）透传代理 ---------------------------------
     * 固件**不解释**屏的协议，只转发 —— 所以改屏的界面完全不用动固件。
     *   LCD <内容>     原样发 + **自动补 FF FF FF**（淘晶驰每条指令必须以此结尾）
     *   LCDRAW <hex>   原样发十六进制字节、**不补**（发二进制/特殊字节时用）
     *   LCDBAUD <n>    运行时切屏侧波特率（出厂常见 9600；⚠️ 不一致时屏毫无反应）
     *   LCDRD          读屏发回来的字节（hex）—— 用来判断"屏到底有没有在说话" */
    if (streq(cmd, "LCD")) {
        if (s_arg_raw[0] == '\0') {
            send_err(19, "lcd needs content");
            return;
        }
        {
            /* 回读实际发出的字节数（= 内容长度 + 3 个 FF）：没有屏时靠它判断
             * 载荷是否完整（`s_arg_raw` 有没有被截断 / 空格有没有丢）。 */
            char buf[40];
            char *q = buf;
            q = put_str(q, "lcd_sent=");
            q = put_u32(q, lcd_send_text(s_arg_raw));
            q = put_str(q, "B");
            *q = '\0';
            send_ok(buf);
        }
        return;
    }
    if (streq(cmd, "LCDRAW")) {
        uint8_t raw[LINE_MAX / 2u];
        uint32_t n = 0;
        const char *h = s_arg_raw;

        while (h[0] != '\0' && h[1] != '\0' && n < (uint32_t)sizeof(raw)) {
            int hi = hex_val(h[0]);
            int lo = hex_val(h[1]);

            if (hi < 0 || lo < 0) {
                send_err(20, "bad hex");
                return;
            }
            raw[n++] = (uint8_t)((hi << 4) | lo);
            h += 2;
        }
        if (n == 0u) {
            send_err(20, "bad hex");
            return;
        }
        lcd_send_bytes(raw, n);
        {
            char buf[32];
            char *q = buf;
            q = put_str(q, "lcd_raw=");
            q = put_u32(q, n);
            *q = '\0';
            send_ok(buf);
        }
        return;
    }
    if (streq(cmd, "LCDBAUD")) {
        /* ⚠️ 2026-10-04 换 C8T6 后：屏改成**软件串口**（PB8），波特率**固定 9600** ✗
         *    —— 高波特率会被 10kHz 的步进中断打乱，所以这个命令**不再能改波特率**，
         *    老实回一句明确的话，别让上位机以为改成功了 ✗。屏侧请自己设 9600 ✓。 */
        if (ntok < 2) {
            send_err(10, "LCDBAUD <n>");
            return;
        }
        send_err(21, "lcd is soft-uart @9600 on PB8 now - set the screen to 9600");
        return;
    }
    if (streq(cmd, "LCDRD")) {
        char buf[192];
        char *q = buf;
        static const char *hexd = "0123456789ABCDEF";
        uint32_t n = 0;
        int b;

        q = put_str(q, "lcd_rx=");
        while ((b = lcd_getbyte()) >= 0 && n < 40u) {
            *q++ = hexd[(b >> 4) & 0xF];
            *q++ = hexd[b & 0xF];
            *q++ = ' ';
            n++;
        }
        q = put_str(q, "count=");
        q = put_u32(q, n);
        *q = '\0';
        send_ok(buf);
        return;
    }

    /* ---- 其它一律明说，不装死 ---- */
    {
        char buf[64];
        char *q = buf;
        q = put_str(q, "unknown ");
        q = put_str(q, cmd);
        *q = '\0';
        send_err(1, buf);
    }
}

/* --------------------------------------------------------------------------
 * 主循环
 * ------------------------------------------------------------------------ */
/* 把某个串口收到的一个字节喂进行缓冲；凑成一行就交给 handle_line 解析。
 *
 * ⚠️ 关键点：**回复要发回"命令来的那个口"**（port 1 = USART1 调试口，port 4 = USART3 视觉板 K210）。
 *    靠 uart_set_out_port() 切换，解析完立刻切回 1，
 *    于是上层的 send_ok/send_err/reply 一行都不用改。
 *    两个口各有一份独立的行缓冲，互不干扰。
 *
 * ⚠️ 2026-10-04：**port 2（无线桥 C6）已停用** ✗（用户决定不用 ESP32）——
 *    它原来的 USART2 现在归**串口屏**所有 ✓，屏回传的字节由 lcd_poll() 收，
 *    **不再**当命令解析（否则屏的触摸事件会被当成命令、还回一堆 ERR 灌回屏里 ✗）。 */
static void feed(char *line, uint32_t *len, uint8_t *overflow, int ch, int port)
{
    char c = (char)ch;

    if (c == '\n' || c == '\r') {
        if (*overflow) {
            *overflow = 0; /* 超长行已经报过错，这里只做收尾 */
        } else if (*len > 0) {
            line[*len] = '\0';
            if (port == 2) {
                /* ⚠️ 2026-10-04：无线桥已停用 ✗，USART2 归串口屏 —— 这里**不再解析**，
                 *    只静默丢弃（屏的触摸回传由 lcd_poll() 收进 LCDRD 缓冲 ✓）。 */
            } else if (port == 4) {
                /* ⭐ 视觉口**只认带前缀 `@` 的行**，其余一律**静默忽略**（不回 ERR）。
                 *
                 * 为什么必须这样：这块 K210 的 `TXD/RXD` 排针就是它的**控制台 UART2**
                 * （与板上 CH340/USB 是同一路）—— 开发时 REPL 的开机横幅（`hello yahboom!`）、
                 * 回显、报错都会同时灌进 STM32 ✗。
                 * 不过滤的话：① STM32 会把它们当命令解析、刷一堆 `ERR 1 unknown` ✗
                 *             ② 更危险的是**误执行**（REPL 输出里若凑出合法命令就会真的动机构）✗✗
                 * 约定：K210 发 `@PING`、`@VIS ...`；STM32 剥掉 `@` 再走同一套解析器。 */
                if (line[0] == '@') {
                    uart_puts("EVT U4_LINE ");
                    uart_puts(line + 1);
                    uart_puts("\r\n");
                    uart_set_out_port(4);
                    handle_line(line + 1);
                    uart_set_out_port(1);
                } else {
                    /* 记一笔但**不回复**，方便诊断"K210 那条线在灌什么" */
                    uart_puts("EVT U4_RAW ");
                    uart_puts(line);
                    uart_puts("\r\n");
                }
                *len = 0;
                return;
            }
            uart_set_out_port(port);
            handle_line(line);
            uart_set_out_port(1);
        }
        *len = 0;
        return;
    }

    if (*overflow) {
        return; /* 丢弃到行尾 */
    }
    if (*len + 1u >= LINE_MAX) {
        *overflow = 1;
        *len = 0;
        uart_set_out_port(port);
        send_err(10, "line too long");
        uart_set_out_port(1);
        return;
    }
    line[(*len)++] = c;
}

int main(void)
{
    /* ---- 全局关中断 ----
     * Cortex-M3 复位后 PRIMASK=0，中断本来就是全局开启的；而 board_init 末尾
     * ⚠️ **2026-09-22 订正（实验 1/2 实测）**：原先这里记的"一开 TICKINT 就卡
     * uart_puts"**是误判**。真相是**向量表下标错位**（见 startup.c 第 89-93 行）：
     * USART1 的向量被放到 IRQn 21、真正的 IRQn 37 是 0 → "开中断 + RX 线上一个噪声
     * 起始位"就跳到地址 0 跑飞；卡死恰好落在开中断后的第一次 uart_puts 上。
     * 该错误已修 → SysTick(TICKINT) 与 USART1 RX 中断**都实测正常**，
     * 故默认 ALLOW_IRQ=1（不关中断），M3 的定时器中断直接可用。 */
#if !ALLOW_IRQ
    __disable_irq();
#endif

    BOOT_MARKER = MARK_MAIN_ENTERED;

#if BAUD_SELFTEST
    /* ------------------------------------------------------------------
     * 波特率自校准（M1 诊断）
     *
     * 关键洞察：**BRR 的物理含义就是"一个 bit 占多少个 HCLK 周期"**
     *           —— 因为 BRR = USARTDIV×16 = f_cpu/(16×baud)×16 = f_cpu/baud。
     * 所以只要测出 PC 发来的**连续字节之间的 HCLK 周期数**，再除以每字节的位数
     * （8E1 = 11 位），结果**直接就是该写的 BRR** —— 完全不需要知道主频多少。
     *
     * 这正是出厂 ROM bootloader 自动测波特率的同一招，也是它为什么能在
     * "晶振频率不明"的板子上照常工作的原因。
     *
     * 测完把 BRR 写进去，紧接着打印测量结果 —— 因为此时波特率已经配对，
     * 这行输出在 PC 上一定是可读的（如果还读不懂，那就不是波特率的问题）。
     * ------------------------------------------------------------------ */
    board_early_uart(); /* 先用试探值 BRR=69 把接收跑起来 */

    /* ------------------------------------------------------------------
     * 波特率自校准（用"总时长"，不用"帧数"）
     *
     * 原理：PC 连发 N 个字节，占用 N×11 个位时间（8E1 = 11 位/字节）。
     *       固件用 SysTick 量出整束的起止时长（HCLK 周期），则
     *              BRR = f/baud = 总周期数 ÷ (N × 11)
     *       —— 又因为 BRR 的物理含义就是"一个 bit 占多少 HCLK 周期"，
     *          这个式子**根本不需要知道主频是多少**。
     *
     * ⚠️ 关键修正（上次失败的真正原因）：**除数必须用"PC 实际发出去的字节数"，
     *    而不是"固件自己数到的帧数"**。波特率失配时接收端会把 N 个字节错帧成
     *    更多帧（上次 64 字节被数成 74 帧），拿它当除数会让算出的 BRR 偏小约 14%
     *    —— 结果当然还是乱码。**"总量÷已知个数"永远比"逐段平均"稳**。
     * ------------------------------------------------------------------ */
#define CAL_BYTES 64u /* PC 每次校准固定发这么多字节（必须与 PC 侧一致） */

    SysTick->LOAD = 0xFFFFFFu; /* 自由运行、不产生中断：只当 HCLK 周期计数器 */
    SysTick->VAL = 0;
    SysTick->CTRL = SysTick_CTRL_CLKSOURCE_Msk | SysTick_CTRL_ENABLE_Msk;
    USART1->CR1 |= USART_CR1_RE; /* 打开接收 */

    {
        uint32_t t_first = 0;
        uint32_t t_last = 0;
        uint32_t got = 0;
        uint32_t idle = 0;
        uint32_t wait = 0;

        /* ① 等第一个字节（开始计时） */
        while (wait < 300000000u) {
            wait++;
            if (USART1->SR & USART_SR_RXNE) {
                (void)USART1->DR;
                t_first = SysTick->VAL;
                t_last = t_first;
                got = 1;
                break;
            }
        }

        /* ② 继续收，直到出现一段足够长的静默（认为整束结束） */
        if (got > 0u) {
            for (;;) {
                if (USART1->SR & USART_SR_RXNE) {
                    (void)USART1->DR;
                    t_last = SysTick->VAL;
                    got++;
                    idle = 0;
                } else {
                    idle++;
                    if (idle > 300000u) { /* 约 0.1 秒静默 → 收完了 */
                        break;
                    }
                }
            }
        }

        char buf[80];
        char *q = buf;
        q = put_str(q, "CAL got=");
        q = put_u32(q, got);

        if (got > 0u) {
            /* SysTick 递减：t_first - t_last = 整束占用的 HCLK 周期数 */
            uint32_t span = (t_first - t_last) & 0xFFFFFFu;
            uint32_t brr = span / ((CAL_BYTES - 1u) * 11u); /* 用已知字节数当除数 */
            q = put_str(q, " span=");
            q = put_u32(q, span);
            q = put_str(q, " brr=");
            q = put_u32(q, brr);
            q = put_str(q, " hclk=");
            q = put_u32(q, brr * 115200u);
            q = put_str(q, "\r\n");
            *q = '\0';
            if (brr >= 1u && brr <= 0xFFFFu) {
                USART1->CR1 = 0;
                USART1->BRR = (uint16_t)brr; /* ← 波特率现在配对了 */
                USART1->CR1 = UART_CR1_8E1;
            }
        } else {
            q = put_str(q, " TIMEOUT（没收到字节）\r\n");
            *q = '\0';
        }
        board_early_puts(buf);
        board_early_puts("CAL done 0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ\r\n");
    }

    for (;;) { } /* 停在这里，等桥把结果读走 */
#else
    board_init();
    BOOT_MARKER = MARK_BOARD_INITED;

#if ALLOW_IRQ
    /* 诊断路标：这一行能出来 = 在**中断已开**的情况下活着走出了 board_init
     * （也就是 systick_init() 设了 TICKINT 之后没有立刻风暴）。
     * 它出不来 = 死在 board_init 内部，再往前一步就是 systick_init()/servo_init()。 */
    uart_puts("LOG diag alive-after-board_init irq=on\r\n");
#endif

    /* ---- 关键寄存器快照进 RAM ----------------------------------------
     * 外设寄存器（RCC/USART）不方便用 bootloader 直接读（实测有的地址会被 NACK），
     * 但 **RAM 一直可读**。所以把应用当时的真实配置抄一份到 RAM，
     * "复位回 bootloader → 读 0x20004810" 就能看到它到底把时钟和串口配成了什么。
     * 地址安排（不与 BOOT_MARKER @0x20004800 冲突）：
     *   +0x10 RCC_CFGR   +0x14 RCC_CR   +0x18 USART1->BRR   +0x1C USART1->CR1
     * ------------------------------------------------------------------ */
    {
        /* ⚠️ 2026-10-04：地址随 BOOT_MARKER 一起从 RCT6 的 0x2000B010 挪到 C8T6 合法的
         *    **0x20004810** —— 原来那个地址超出 20KB RAM ✗，一写就 HardFault ✓（见 boot_marker.h） */
        volatile uint32_t *dump = (volatile uint32_t *)0x20004810u;
        dump[0] = RCC->CFGR;
        dump[1] = RCC->CR;
        dump[2] = (uint32_t)USART1->BRR;
        dump[3] = (uint32_t)USART1->CR1;
    }

    s_state = ST_IDLE_UNHOMED; /* M1 没有上电自检，直接进"未回零空闲" */

    /* ---- 波特率自检（诊断代码，已结案，默认关闭）--------------------------
     * 同一句话用 9 个不同的 BRR 各发一遍。只有 BRR 恰好等于 f_cpu / 115200 的那一遍，
     * 在 PC 上才能读通 → **读到哪一行就知道 f_cpu = 115200 × 那个 BRR**。
     *
     * 🎯 2026-09-22 结案记录：打开后实测 **只有 `PROBE brr=69` 那一行是干净的**
     *    → f_cpu = 115200 × 69 = 7.95MHz，即 8MHz 主频没问题、BRR=69 也一直是对的。
     *    这条结论把"波特率/主频偏差"彻底排除，从而把注意力逼到**帧格式**上，
     *    最终查出真凶是 CR1.M 的字长语义（见 board.h 的 UART_CR1_8E1 注释）。
     *
     * ⚠️ 注意：这段代码会**用错误的 BRR 连续发 9 行**，所以打开时 boot 输出会
     *    有约 6 秒的乱码。正式固件保持关闭。 */
#define PROBE_BOOT 0
#if PROBE_BOOT
    {
        static const uint16_t probes[9] = {55, 60, 65, 69, 75, 80, 85, 89, 95};
        BOOT_MARKER = MARK_PROBE_ENTER;
        for (int i = 0; i < 9; i++) {
            char buf[64];
            char *q = buf;
            q = put_str(q, "PROBE brr=");
            BOOT_MARKER = MARK_PROBE_STR + (uint32_t)i;
            q = put_u32(q, probes[i]);
            BOOT_MARKER = MARK_PROBE_NUM + (uint32_t)i;
            q = put_str(q, " abcdefghijklmnopqrstuvwxyz 0123456789\r\n");
            *q = '\0';
            USART1->CR1 = 0;
            USART1->BRR = probes[i];
            USART1->CR1 = UART_CR1_8E1;
            uart_puts(buf);
            BOOT_MARKER = MARK_PROBE_SENT + (uint32_t)i;
            for (volatile uint32_t d = 0; d < 400000u; d++) { } /* 拉开间隔便于分辨 */
        }
        BOOT_MARKER = MARK_PROBE_DONE;
        /* 恢复成"按 8MHz 算"的正常配置 */
        USART1->CR1 = 0;
        USART1->BRR = (8000000u + 115200u / 2u) / 115200u;
        USART1->CR1 = UART_CR1_8E1_RX;
    }
#endif /* PROBE_BOOT */

    /* 上电横幅：这是"固件真的跑起来了"的第一手证据（桥的 serial_read 能看到），
     * 不依赖我们主动发命令。 */
    reply("LOG boot fw=" FW_VERSION " proto=" PROTO_VERSION);
    BOOT_MARKER = MARK_BANNER_DONE;

#if HEARTBEAT
    /* ------------------------------------------------------------------
     * 心跳诊断：持续重复输出一行**已知内容**，每 1 秒一行。
     *
     * 为什么需要它：排查"串口乱码"最痛的一点是**固件只在启动瞬间说话**，
     * 想抓那几十个字节就得先复位一次，而这块板子的复位通路（CH340 自动 ISP
     * 电路 → NRST）到底靠 DTR 还是 RTS、是电平还是边沿，一直没搞清楚 ——
     * 于是每一轮排查都在"复位没成功 / 抓到 0 字节"上反复浪费。
     * 改成持续输出后，**任何时候打开串口都能立刻看到结果**，不需要复位。
     *
     * 内容里同时放数字和大小写字母：数字能看出"高位有没有被多置 1"
     * （波特率偏差的典型指纹），字母能看出整体可读性。
     * 排查结束后把 HEARTBEAT 改回 0 即可。
     * ------------------------------------------------------------------ */
    for (;;) {
        uart_puts("HB 0123456789 abcdefghijklmnopqrstuvwxyz ABCDEFGHIJKLMNOPQRSTUVWXYZ\r\n");
        for (volatile uint32_t d = 0; d < 900000u; d++) { }
    }
#endif

    /* 三个串口各一份行缓冲：USART1 = 板载调试口（8E1）、USART2 = 无线桥 C6（8N1）、
     * UART4 = 视觉板 K210（8N1）。**共用同一个解析器**，回复按"命令从哪来"原路回去。
     * ⚠️ 2026-10-03：必须 **static**（不放栈上）—— 三个 160 字节共 480B 曾把 2KB 的栈压爆，
     *    表现为"一收 UART4 数据就 HardFault / CFSR=INVSTATE"。栈现在也提到 8KB（见 m1.scf）。 */
    static char line[LINE_MAX];
    uint32_t len = 0;
    uint8_t overflow = 0;
    static char line2[LINE_MAX];
    uint32_t len2 = 0;
    uint8_t overflow2 = 0;
    static char line3[LINE_MAX];
    uint32_t len3 = 0;
    uint8_t overflow3 = 0;

    /* ⚠️ 2026-10-04：无线桥停用后，line2/len2/overflow2 暂时没用了 —— 显式标记一下，
     *    免得编译报 unused（留着是因为将来可能恢复无线链路，或者给第 4 个口用）✓ */
    (void)line2;
    (void)len2;
    (void)overflow2;

    /* M2：把舵机与入盒的**真实初值**同步进状态变量。
     * 不初始化的话 STATUS/GETPOS 会报 z=0 grip=0、box=000000，
     * 看起来像"舵机在 0µs 位置"，其实是假数据。 */
    s_servo_z = SERVO_SAFE_Z_US;
    s_servo_grip = SERVO_SAFE_GRIP_US;
    s_box_mask = box_read();
    /* M2：消抖状态机的初值 —— 认为"当前读数已经稳定"，避免开机就报一条假事件 */
    s_box_cand = s_box_mask;
    s_box_cnt = BOX_STABLE_N;
    s_box_last_us = (uint16_t)TIM3->CNT;

    for (;;) {
        /* ⭐ 心跳灯（PC13，低电平点亮）：每 500ms 翻转一次 ✓
         *   = "主循环还活着"的最直观证据（换板/查死机时第一个看它 ✓） */
        {
            static uint32_t led_ms;
            uint32_t now = board_millis();
            if (now - led_ms >= 500u) {
                led_ms = now;
                GPIOC->ODR ^= (1u << 13);
            }
        }

        /* ---- M2+：串口屏回读（触摸事件等）--------------------------------- */
        lcd_poll();

        /* ---- M2：入盒挡光轮询 ---------------------------------------------
         * 没有传感器接进来时 PC0~PC5 被内部上拉成高 → box_read() 恒为 0，
         * 不会产生任何事件（所以空板子也不会刷屏）。
         *
         * 消抖（2026-09-22 按实测数据改）：原实现"状态一变就隔 1ms 再采一次"不够 ——
         * 实测在 1ms 内连跳三次。现改为每 BOX_SAMPLE_MS(5ms) 采一次、
         * 连续 BOX_STABLE_N(4) 次一致才认账（=20ms），口径见《设计》§6。
         * ⚠️ 不用阻塞忙等（UART 只有 2 字节缓冲，阻塞 20ms 会吃掉上百字节命令）；
         * 时间基准用已在跑的 TIM3（1MHz → 1tick = 1µs），也刻意不碰 SysTick 中断。 */
        {
            uint16_t box_now_ticks = (uint16_t)TIM3->CNT;
            if ((uint16_t)(box_now_ticks - s_box_last_us) >= BOX_SAMPLE_MS * 1000u) {
                s_box_last_us = box_now_ticks;
                uint8_t now = box_read();
                if (now == s_box_cand) {
                    if (s_box_cnt < BOX_STABLE_N) { s_box_cnt++; }
                } else {
                    s_box_cand = now;
                    s_box_cnt = 0;
                }
                if (s_box_cnt >= BOX_STABLE_N && s_box_cand != s_box_mask) {
                    s_box_mask = s_box_cand;
                    char evt[32];
                    char *q = evt;
                    q = put_str(q, "EVT BOX_CHANGED box=");
                    for (int i = 5; i >= 0; i--) {
                        *q++ = (char)('0' + ((s_box_mask >> i) & 1u));
                    }
                    *q = '\0';
                    reply(evt);
                }
            }
        }

        /* 两个命令口喂给同一个解析器；回复由 feed() 发回**命令来的那个口** */
        int ch = uart_getbyte();
        if (ch >= 0) {
            feed(line, &len, &overflow, ch, 1);
        }
        /* ⚠️ 2026-10-04：**不再读 u2_getbyte()** ✗ —— USART2 现在是串口屏 ✓，
         *    它的回传由 lcd_poll() 收进 LCDRD 缓冲；若还喂进解析器，
         *    屏的触摸事件会被当命令、并把 `ERR ...` 灌回屏里 ✗。 */
        ch = u4_getbyte(); /* M4：视觉板 K210（**USART3 = PB10/PB11**，C8T6 无 UART4） */
        if (ch >= 0) {
            feed(line3, &len3, &overflow3, ch, 4);
        }
    }
#endif /* BAUD_SELFTEST */
}
