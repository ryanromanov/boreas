/* The runtime of programs compiled by `boreas build`: checked arithmetic, number printing that matches the interpreter
   digit for digit, and the error report. Included once at the top of every generated file. Compile with
   -O2 -std=c11 -ffp-contract=off (never -ffast-math), and -fwrapv; -DBOREAS_RELEASE drops the integer checks,
   -DBOREAS_IEEE is `use ieee` (real arithmetic never stops the program). */
#include <stdint.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <math.h>
#include <sys/stat.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>
#include <float.h>

typedef __int128 rt_i128;

static void rt_fail(int line, const char *fmt, ...) __attribute__((noreturn, format(printf, 2, 3)));
static void rt_fail(int line, const char *fmt, ...) {
    fflush(stdout);
    fprintf(stderr, "error: line %d: ", line);
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}

/* ---- numbers as text ---------------------------------------------------------------------------------------- */

/* a 128-bit integer, for the numbers an overflow message has to show */
static const char *rt_i128_text(rt_i128 v, char *buf) {
    char tmp[64];
    int n = 0;
    bool neg = v < 0;
    unsigned __int128 u = neg ? -(unsigned __int128)v : (unsigned __int128)v;
    if (u == 0) tmp[n++] = '0';
    while (u > 0) { tmp[n++] = (char)('0' + (int)(u % 10)); u /= 10; }
    int k = 0;
    if (neg) buf[k++] = '-';
    while (n > 0) buf[k++] = tmp[--n];
    buf[k] = 0;
    return buf;
}

/* Python's repr of a float: the shortest digits that read back exactly, scientific form when the exponent is
   below -4 or at least 16 (1e-05, 1e+16), a whole number keeps its .0; NaN and Infinity as the interpreter writes them */
static const char *rt_real_text(double x, char *out) {
    if (x != x) { strcpy(out, "NaN"); return out; }
    if (isinf(x)) { strcpy(out, x > 0 ? "Infinity" : "-Infinity"); return out; }
    char sci[40];
    int digits = 1;
    for (; digits <= 17; digits++) {
        snprintf(sci, sizeof sci, "%.*e", digits - 1, x);
        if (strtod(sci, NULL) == x) break;
    }
    if (digits > 17) { digits = 17; snprintf(sci, sizeof sci, "%.16e", x); }
    /* sci is [-]d[.ddd]e[+-]XX */
    char mant[24];
    int m = 0, neg = 0;
    const char *p = sci;
    if (*p == '-') { neg = 1; p++; }
    while (*p && *p != 'e') { if (*p != '.') mant[m++] = *p; p++; }
    mant[m] = 0;
    int exp10 = atoi(p + 1);
    while (m > 1 && mant[m - 1] == '0') mant[--m] = 0;      /* trailing zeros are not digits */
    int decpt = exp10 + 1;                                   /* digits before the point */
    char *o = out;
    if (neg) *o++ = '-';
    if (decpt > -4 && decpt <= 16) {                         /* plain notation */
        if (decpt <= 0) {
            *o++ = '0'; *o++ = '.';
            for (int i = 0; i < -decpt; i++) *o++ = '0';
            for (int i = 0; i < m; i++) *o++ = mant[i];
        } else if (decpt >= m) {
            for (int i = 0; i < m; i++) *o++ = mant[i];
            for (int i = m; i < decpt; i++) *o++ = '0';
            *o++ = '.'; *o++ = '0';
        } else {
            for (int i = 0; i < decpt; i++) *o++ = mant[i];
            *o++ = '.';
            for (int i = decpt; i < m; i++) *o++ = mant[i];
        }
        *o = 0;
    } else {                                                 /* scientific notation */
        *o++ = mant[0];
        if (m > 1) { *o++ = '.'; for (int i = 1; i < m; i++) *o++ = mant[i]; }
        sprintf(o, "e%c%02d", exp10 < 0 ? '-' : '+', abs(exp10));
    }
    return out;
}

/* ---- printing ------------------------------------------------------------------------------------------------ */

static FILE *rt_out;                                  /* where the writers print: the screen, or a file being written */
static void rt_write_int(int64_t v) { fprintf(rt_out, "%lld", (long long)v); }
static void rt_write_real(double v) { char b[48]; fputs(rt_real_text(v, b), rt_out); }
/* a real32 is written as the shortest text that reads back as the same single-precision value, then as a real is
   (100.0, 1e-05, 0.1): the digits are tried as the interpreter tries them, parsed as a double and rounded to single */
static const char *rt_real32_text(float x, char *out) {
    if (x != x || isinf(x)) return rt_real_text((double)x, out);
    char b[48];
    double d = 0.0;
    for (int digits = 1; digits <= 9; digits++) {
        snprintf(b, sizeof b, "%.*g", digits, (double)x);
        d = strtod(b, NULL);
        if ((float)d == x) break;
    }
    return rt_real_text(d, out);
}
static void rt_write_real32(float v) { char b[48]; fputs(rt_real32_text(v, b), rt_out); }
static void rt_write_bool(bool v) { fputs(v ? "true" : "false", rt_out); }
static void rt_write_str(const char *s) { fputs(s, rt_out); }
static void rt_write_sep(void) { fputc(' ', rt_out); }
static void rt_write_nl(void) { fputc('\n', rt_out); }
static void rt_stop(int code) { fflush(stdout); exit(code); }

/* ---- 64-bit integers: checked unless -DBOREAS_RELEASE -------------------------------------------------------- */

static inline void rt_overflow(int line, int64_t a, char op, int64_t b) {
    rt_fail(line, "integer overflow: %lld %c %lld does not fit in 64 bits (use %c%% to wrap)", (long long)a, op, (long long)b, op);
}
#ifndef BOREAS_RELEASE
static inline int64_t rt_add(int64_t a, int64_t b, int line) { int64_t r; if (__builtin_add_overflow(a, b, &r)) rt_overflow(line, a, '+', b); return r; }
static inline int64_t rt_sub(int64_t a, int64_t b, int line) { int64_t r; if (__builtin_sub_overflow(a, b, &r)) rt_overflow(line, a, '-', b); return r; }
static inline int64_t rt_mul(int64_t a, int64_t b, int line) { int64_t r; if (__builtin_mul_overflow(a, b, &r)) rt_overflow(line, a, '*', b); return r; }
static inline int64_t rt_neg(int64_t a, int line) {
    if (a == INT64_MIN) { char b[48]; rt_fail(line, "negating %lld: %s does not fit in int (64 bits)", (long long)a, rt_i128_text(-(rt_i128)a, b)); }
    return -a;
}
#else
static inline int64_t rt_add(int64_t a, int64_t b, int line) { (void)line; return (int64_t)((uint64_t)a + (uint64_t)b); }
static inline int64_t rt_sub(int64_t a, int64_t b, int line) { (void)line; return (int64_t)((uint64_t)a - (uint64_t)b); }
static inline int64_t rt_mul(int64_t a, int64_t b, int line) { (void)line; return (int64_t)((uint64_t)a * (uint64_t)b); }
static inline int64_t rt_neg(int64_t a, int line) { (void)line; return (int64_t)(0 - (uint64_t)a); }
#endif
static inline int64_t rt_div(int64_t a, int64_t b, int line) {      /* truncates toward zero, like Fortran */
    if (b == 0) rt_fail(line, "integer division by zero");
    if (a == INT64_MIN && b == -1) {
#ifndef BOREAS_RELEASE
        rt_overflow(line, a, '/', b);
#else
        return a;
#endif
    }
    return a / b;
}
static inline int64_t rt_mod(int64_t a, int64_t b, int line) {      /* mod(a, b): the sign of a */
    if (b == 0) rt_fail(line, "mod(%lld, 0): division by zero", (long long)a);
    if (b == -1) return 0;
    return a % b;
}
/* Bit functions. A whole number travels as an int64_t; w is the width of its kind (32 or 64). The bits are worked on
   as an unsigned pattern of that width, and the result is the signed value of the pattern. */
static inline uint64_t rt_bu(int64_t x, int w) { return w == 32 ? (uint64_t)(uint32_t)x : (uint64_t)x; }
static inline int64_t rt_bs(uint64_t u, int w) { return w == 32 ? (int64_t)(int32_t)(uint32_t)u : (int64_t)u; }
static inline int64_t rt_iand(int64_t a, int64_t b, int w) { return rt_bs(rt_bu(a, w) & rt_bu(b, w), w); }
static inline int64_t rt_ior(int64_t a, int64_t b, int w) { return rt_bs(rt_bu(a, w) | rt_bu(b, w), w); }
static inline int64_t rt_ieor(int64_t a, int64_t b, int w) { return rt_bs(rt_bu(a, w) ^ rt_bu(b, w), w); }
static inline int64_t rt_bitnot(int64_t a, int w) { return rt_bs(~rt_bu(a, w), w); }
static inline int64_t rt_ishft(int64_t x, int64_t n, int w, int line) {
    if (n < -w || n > w) rt_fail(line, "ishft(): the shift must be a whole number from -%d to %d", w, w);
    uint64_t u = rt_bu(x, w);
    return rt_bs(n >= 0 ? (n >= 64 ? 0 : u << n) : (-n >= 64 ? 0 : u >> -n), w);
}
static inline int64_t rt_shift(char dir, const char *name, int64_t x, int64_t n, int w, int line) {
    if (n < 0 || n > w) rt_fail(line, "%s(): the shift must be a whole number from 0 to %d", name, w);
    uint64_t u = rt_bu(x, w);
    if (dir == 'l') return rt_bs(n >= 64 ? 0 : u << n, w);
    if (dir == 'r') return rt_bs(n >= 64 ? 0 : u >> n, w);
    return n >= w ? (x < 0 ? -1 : 0) : (x >> n);            /* shifta: the sign bit fills in */
}
static inline int rt_bpos(const char *name, int64_t pos, int w, int line) {
    if (pos < 0 || pos >= w) rt_fail(line, "%s(): the bit position must be a whole number from 0 to %d", name, w - 1);
    return (int)pos;
}
static inline bool rt_btest(int64_t x, int64_t pos, int w, int line) { return (rt_bu(x, w) >> rt_bpos("btest", pos, w, line)) & 1; }
static inline int64_t rt_ibset(int64_t x, int64_t pos, int w, int line) { return rt_bs(rt_bu(x, w) | (1ULL << rt_bpos("ibset", pos, w, line)), w); }
static inline int64_t rt_ibclr(int64_t x, int64_t pos, int w, int line) { return rt_bs(rt_bu(x, w) & ~(1ULL << rt_bpos("ibclr", pos, w, line)), w); }
static inline int64_t rt_ibits(int64_t x, int64_t pos, int64_t len, int w, int line) {
    int p = rt_bpos("ibits", pos, w, line);
    if (len < 0 || len > w - p) rt_fail(line, "ibits(): the length must be a whole number from 0 to %d", w - p);
    return rt_bs((rt_bu(x, w) >> p) & (len >= 64 ? ~0ULL : (1ULL << len) - 1), w);
}
static inline int64_t rt_popcnt(int64_t x, int w) { return __builtin_popcountll(rt_bu(x, w)); }
static inline int64_t rt_leadz(int64_t x, int w) { uint64_t u = rt_bu(x, w); return u == 0 ? w : __builtin_clzll(u) - (64 - w); }
static inline int64_t rt_trailz(int64_t x, int w) { uint64_t u = rt_bu(x, w); return u == 0 ? w : __builtin_ctzll(u); }
/* norm2 adds the squares exactly (Python's math.fsum: Shewchuk's algorithm, rounded once at the end), so the length
   does not depend on the order of the numbers. This is a port of CPython's math_fsum. */
typedef struct { double small[32]; double *p; int64_t n, cap; double special, inf; } rt_fsum;
static void rt_fsum_init(rt_fsum *f) { f->p = f->small; f->n = 0; f->cap = 32; f->special = 0.0; f->inf = 0.0; }
static void rt_fsum_add(rt_fsum *f, double x, int line) {
    double xsave = x, y, t, hi, yr, lo;
    int64_t i = 0;
    for (int64_t j = 0; j < f->n; j++) {
        y = f->p[j];
        if (fabs(x) < fabs(y)) { t = x; x = y; y = t; }
        hi = x + y; yr = hi - x; lo = y - yr;
        if (lo != 0.0) f->p[i++] = lo;
        x = hi;
    }
    f->n = i;
    if (x != 0.0) {
        if (!isfinite(x)) {
            if (isfinite(xsave)) rt_fail(line, "norm2(): intermediate overflow in fsum");
            if (isinf(xsave)) f->inf += xsave;
            f->special += xsave;
            f->n = 0;
        } else {
            if (f->n >= f->cap) {
                int64_t cap = f->cap * 2;
                double *q = (double *)malloc((size_t)cap * sizeof(double));
                if (!q) rt_fail(line, "out of memory");
                memcpy(q, f->p, (size_t)f->n * sizeof(double));
                if (f->p != f->small) free(f->p);
                f->p = q; f->cap = cap;
            }
            f->p[f->n++] = x;
        }
    }
}
static double rt_fsum_result(rt_fsum *f, int line) {
    double hi = 0.0, lo = 0.0, x, y, yr;
    if (f->special != 0.0) {
        if (isnan(f->inf)) rt_fail(line, "norm2(): -inf + inf in fsum");
        hi = f->special;
    } else if (f->n > 0) {
        int64_t n = f->n;
        hi = f->p[--n];
        while (n > 0) {
            x = hi; y = f->p[--n];
            hi = x + y; yr = hi - x; lo = y - yr;
            if (lo != 0.0) break;
        }
        if (n > 0 && ((lo < 0.0 && f->p[n - 1] < 0.0) || (lo > 0.0 && f->p[n - 1] > 0.0))) {
            y = lo * 2.0; x = hi + y; yr = x - hi;
            if (y == yr) hi = x;
        }
    }
    if (f->p != f->small) free(f->p);
    return hi;
}
static int64_t rt_ipow(int64_t a, int64_t b, int line) {
    if (b < 0) {
        if (a == 0) rt_fail(line, "zero cannot be raised to a negative integer power");
        if (a == 1) return 1;
        if (a == -1) return (b % 2 == 0) ? 1 : -1;
        return 0;
    }
    if (a == 0) return b == 0 ? 1 : 0;
    if (a == 1) return 1;
    if (a == -1) return (b % 2 == 0) ? 1 : -1;
    rt_i128 result = 1;
    bool big = false;
    for (int64_t i = 0; i < b && !big; i++) {          /* |a| >= 2: gives up after about 100 steps */
        result *= a;
        if (result > ((rt_i128)1 << 100) || result < -((rt_i128)1 << 100)) big = true;
    }
    if (big || result > INT64_MAX || result < INT64_MIN) {
#ifndef BOREAS_RELEASE
        char t[64];
        if (big) rt_fail(line, "%lld ** %lld: the result is too large for an int (64 bits)", (long long)a, (long long)b);
        rt_fail(line, "%lld ** %lld: %s does not fit in int (64 bits)", (long long)a, (long long)b, rt_i128_text(result, t));
#else
        (void)line;
#endif
    }
    return (int64_t)result;
}

/* ---- reals: an operation that turns finite numbers into Infinity stops the program unless `use ieee` ------------ */

/* the numbers in a message: an int argument is shown as an int (sqrt(-4)), a real as Python shows it (sqrt(-4.0)) */
static const char *rt_shown_override = NULL;       /* set while a function runs over a whole array: the message names the array */
static const char *rt_shown(double x, int was_int, char *buf) {
    if (rt_shown_override) { strcpy(buf, rt_shown_override); return buf; }
    if (was_int) snprintf(buf, 48, "%lld", (long long)x);
    else if (x != x) strcpy(buf, "nan");                  /* (Python's own words in a message: inf, -inf, nan) */
    else if (isinf(x)) strcpy(buf, x < 0 ? "-inf" : "inf");
    else rt_real_text(x, buf);
    return buf;
}
#ifdef BOREAS_IEEE
static inline double rt_fadd(double a, double b, int flags, int line) { (void)flags; (void)line; return a + b; }
static inline double rt_fsub(double a, double b, int flags, int line) { (void)flags; (void)line; return a - b; }
static inline double rt_fmul(double a, double b, int flags, int line) { (void)flags; (void)line; return a * b; }
static inline double rt_fdiv(double a, double b, int flags, int line) { (void)flags; (void)line; return a / b; }
static inline double rt_fpow(double a, double b, int flags, int line) { (void)flags; (void)line; return pow(a, b); }
#else
/* flags: bit 0 set when the left operand was a whole number, bit 1 when the right one was (the message shows it so) */
static inline double rt_checked(double r, double a, char op, double b, int flags, int line) {
    if (isinf(r) && isfinite(a) && isfinite(b)) {
        char x[48], y[48];
        rt_fail(line, "%s %c %s is too large for a real (use 'use ieee' to get Infinity)", rt_shown(a, flags & 1, x), op, rt_shown(b, flags & 2, y));
    }
    return r;
}
static inline double rt_fadd(double a, double b, int flags, int line) { return rt_checked(a + b, a, '+', b, flags, line); }
static inline double rt_fsub(double a, double b, int flags, int line) { return rt_checked(a - b, a, '-', b, flags, line); }
static inline double rt_fmul(double a, double b, int flags, int line) { return rt_checked(a * b, a, '*', b, flags, line); }
static inline double rt_fdiv(double a, double b, int flags, int line) {
    if (b == 0.0) rt_fail(line, "division by zero");
    return rt_checked(a / b, a, '/', b, flags, line);
}
static inline double rt_fpow(double a, double b, int flags, int line) {
    char x[48], y[48];
    if (a == 0.0 && b < 0.0) rt_fail(line, "division by zero");
    if (a < 0.0 && b != floor(b) && isfinite(b))
        rt_fail(line, "(%s) ** %s is not a real number (a negative number to a fractional power); for a complex root write cmplx(a, 0.0) ** b",
                rt_shown(a, flags & 1, x), rt_shown(b, flags & 2, y));
    double r = pow(a, b);
    if (isinf(r) && isfinite(a) && isfinite(b)) rt_fail(line, "%s ** %s is too large", rt_shown(a, flags & 1, x), rt_shown(b, flags & 2, y));
    return r;
}
#endif

/* a library function of one real: no real answer, or too large, stops the program unless `use ieee` */
static inline double rt_math1(const char *name, double r, double x, int was_int, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (r != r && x == x) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown(x, was_int, b));
    if (isinf(r) && isfinite(x)) rt_fail(line, "%s(%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", name, rt_shown(x, was_int, b));
#else
    (void)was_int;
    if (isinf(x) && (strcmp(name, "sin") == 0 || strcmp(name, "cos") == 0)) {
        char b[48];
        rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown(x, 0, b));
    }
#endif
    return r;
}
/* The Bessel functions are called through volatile pointers: a compiler that knows j0 and jn (gcc does) works a constant argument out
   itself, correctly rounded (and, for jn of a big order, very slowly), where the C library's own answer differs in the last digit. */
static double (*volatile rt_fp_j0)(double) = j0;
static double (*volatile rt_fp_j1)(double) = j1;
static double (*volatile rt_fp_y0)(double) = y0;
static double (*volatile rt_fp_y1)(double) = y1;
static double (*volatile rt_fp_jn)(int, double) = jn;
static double (*volatile rt_fp_yn)(int, double) = yn;
/* the C library's Bessel functions, with the interpreter's errors: NaN (and y at 0) has no real answer, any other infinity is too large */
static double rt_bessel(const char *name, double r, bool is_y, bool has_n, int64_t n, double x, int was_int, int line) {
    if (r == r && !isinf(r)) return r;
    bool domain = r != r || (is_y && x == 0.0);
#ifdef BOREAS_IEEE
    (void)name; (void)has_n; (void)n; (void)was_int; (void)line;
    if (!domain) return INFINITY;
    return r == r ? -INFINITY : NAN;
#else
    char b[48], nb[32];
    if (has_n) snprintf(nb, sizeof nb, "%lld, ", (long long)n); else nb[0] = 0;
    if (domain) rt_fail(line, "%s(%s%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, nb, rt_shown(x, was_int, b));
    rt_fail(line, "%s(%s%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", name, nb, rt_shown(x, was_int, b));
    return r;
#endif
}
static inline double rt_sqrt(double x, int was_int, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (x < 0.0) rt_fail(line, "sqrt(%s) has no real answer (a file that says `use ieee` gets NaN instead)", rt_shown(x, was_int, b));
#else
    (void)was_int; (void)line;
#endif
    return sqrt(x);
}
static inline double rt_log(double x, double (*fn)(double), const char *name, int was_int, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (x <= 0.0) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown(x, was_int, b));
#else
    (void)name; (void)was_int; (void)line;
#endif
    return fn(x);
}
static inline double rt_exp(double x, int was_int, int line) { return rt_math1("exp", exp(x), x, was_int, line); }
static inline double rt_fmod(double a, double b, int a_int, int b_int, int line) {
#ifdef BOREAS_IEEE
    (void)a_int; (void)b_int; (void)line;
#else
    if (b == 0.0 || (isinf(a) && b == b)) { char x[48], y[48]; rt_fail(line, "mod(%s, %s): division by zero", rt_shown(a, a_int, x), rt_shown(b, b_int, y)); }
#endif
    return fmod(a, b);
}
static inline int64_t rt_abs(int64_t a, int line) {
    if (a == INT64_MIN) { char b[48]; rt_fail(line, "abs(): integer overflow (%s does not fit in 64 bits)", rt_i128_text(-(rt_i128)a, b)); }
    return a < 0 ? -a : a;
}
static inline int64_t rt_trunc_int(double x, int line) {
    if (x != x || isinf(x)) rt_fail(line, "int(): cannot convert float %s to integer", x != x ? "NaN" : "infinity");     /* (Python's text has no sign) */
    double t = trunc(x);
    if (t >= 9223372036854775808.0 || t < -9223372036854775808.0) {
        char b[400];
        snprintf(b, sizeof b, "%.0f", t);
        rt_fail(line, "int(): %s does not fit in int (64 bits)", b);
    }
    return (int64_t)t;
}
static inline void rt_check_step(int64_t step, int line) { if (step == 0) rt_fail(line, "do step cannot be zero"); }

/* a call is one more level of nesting: the interpreter stops at 10000, so does a checked program (not a release one) */
static int rt_call_line = 0;       /* the line of the call being made: a function that ends without returning reports it */
#ifndef BOREAS_RELEASE
static int rt_depth = 0;
static inline void rt_call_in(int line, const char *name) {
    rt_call_line = line;
    if (++rt_depth > 10000) rt_fail(line, "'%s' is nested more than 10000 calls deep: a recursive call that never stops?", name);
}
static inline void rt_call_out(void) { rt_depth--; }
#else
static inline void rt_call_in(int line, const char *name) { rt_call_line = line; (void)name; }
static inline void rt_call_out(void) {}
#endif

/* ---- 32-bit integers --------------------------------------------------------------------------------------------- */

#ifndef BOREAS_RELEASE
static inline int32_t rt_fit32(int64_t r, int64_t a, char op, int64_t b, int line) {
    if (r < INT32_MIN || r > INT32_MAX) rt_fail(line, "%lld %c %lld: %lld does not fit in int32 (32 bits)", (long long)a, op, (long long)b, (long long)r);
    return (int32_t)r;
}
#else
static inline int32_t rt_fit32(int64_t r, int64_t a, char op, int64_t b, int line) { (void)a; (void)op; (void)b; (void)line; return (int32_t)r; }
#endif
static inline int32_t rt_add32(int32_t a, int32_t b, int line) { return rt_fit32((int64_t)a + b, a, '+', b, line); }
static inline int32_t rt_sub32(int32_t a, int32_t b, int line) { return rt_fit32((int64_t)a - b, a, '-', b, line); }
static inline int32_t rt_mul32(int32_t a, int32_t b, int line) { return rt_fit32((int64_t)a * b, a, '*', b, line); }
static inline int32_t rt_div32(int32_t a, int32_t b, int line) {
    if (b == 0) rt_fail(line, "integer division by zero");
    return rt_fit32((int64_t)a / b, a, '/', b, line);
}
static inline int32_t rt_neg32(int32_t a, int line) {
#ifndef BOREAS_RELEASE
    if (a == INT32_MIN) rt_fail(line, "negating %d: %lld does not fit in int32 (32 bits)", a, -(long long)a);
#endif
    (void)line;
    return (int32_t)(0 - (uint32_t)a);
}
static inline int32_t rt_mod32(int32_t a, int32_t b, int line) {
    if (b == 0) rt_fail(line, "mod(%d, 0): division by zero", a);
    if (b == -1) return 0;
    return a % b;
}
static inline int32_t rt_abs32(int32_t a, int line) {
    if (a == INT32_MIN) rt_fail(line, "abs(): integer overflow (%lld does not fit in 32 bits)", -(long long)a);
    return a < 0 ? -a : a;
}
/* a whole number stored where an int32 is wanted (an assignment, an argument, a result): checked against the value */
static inline int32_t rt_narrow32(int64_t v, int line, const char *what) {
#ifndef BOREAS_RELEASE
    if (v < INT32_MIN || v > INT32_MAX) {
        if (what[0]) rt_fail(line, "%s: %lld does not fit in int32 (32 bits)", what, (long long)v);
        rt_fail(line, "%lld does not fit in int32 (32 bits)", (long long)v);
    }
#endif
    (void)line; (void)what;
    return (int32_t)v;
}

/* ---- arrays: column-major, 1-based, at most three dimensions, owned by the variable that holds them --------------- */

#define RT_DEFINE_ARRAY(NAME, T)                                                                                   \
    typedef struct { T *data; int64_t shape[3]; int rank; int64_t n; } rt_##NAME##_arr;                              \
    static inline void rt_free_##NAME(rt_##NAME##_arr *a) { free(a->data); a->data = NULL; a->n = 0; }              \
    static void rt_alloc_##NAME(rt_##NAME##_arr *a, int rank, int64_t d0, int64_t d1, int64_t d2, int line) {       \
        if (d0 < 0 || d1 < 0 || d2 < 0) rt_fail(line, "array dimensions must be non-negative integers");           \
        free(a->data);                                                                                             \
        a->rank = rank; a->shape[0] = d0; a->shape[1] = d1; a->shape[2] = d2;                                      \
        a->n = d0 * d1 * d2;                                                                                       \
        a->data = (T *)calloc(a->n ? (size_t)a->n : 1, sizeof(T));      /* never NULL once allocated, even when empty */ \
        if (!a->data) rt_fail(line, "out of memory");                                                              \
    }                                                                                                              \
    static inline void rt_like_##NAME(rt_##NAME##_arr *a, const int64_t *shape, int rank, int line) {              \
        rt_alloc_##NAME(a, rank, shape[0], rank > 1 ? shape[1] : 1, rank > 2 ? shape[2] : 1, line);                 \
    }
typedef struct { double re, im; } rt_cx;                 /* a complex number: two doubles (never C's _Complex: Python's arithmetic differs) */
RT_DEFINE_ARRAY(int, int64_t)
RT_DEFINE_ARRAY(int32, int32_t)
RT_DEFINE_ARRAY(real, double)
RT_DEFINE_ARRAY(real32, float)
RT_DEFINE_ARRAY(logical, bool)
RT_DEFINE_ARRAY(complex, rt_cx)

/* a growing list of values (an array literal with a generator or a spliced array does not know its length until it has run) */
#define RT_DEFINE_VEC(NAME, T)                                                                                     \
    typedef struct { T *p; int64_t n, cap; } rt_vec_##NAME;                                                        \
    static inline void rt_free_vec_##NAME(rt_vec_##NAME *v) { free(v->p); v->p = NULL; v->n = v->cap = 0; }         \
    static inline void rt_push_##NAME(rt_vec_##NAME *v, T x, int line) {                                           \
        if (v->n == v->cap) {                                                                                      \
            int64_t c = v->cap ? v->cap * 2 : 16;                                                                  \
            T *q = (T *)realloc(v->p, (size_t)c * sizeof(T));                                                      \
            if (!q) rt_fail(line, "out of memory");                                                                \
            v->p = q; v->cap = c;                                                                                  \
        }                                                                                                          \
        v->p[v->n++] = x;                                                                                          \
    }
RT_DEFINE_VEC(int, int64_t)
RT_DEFINE_VEC(int32, int32_t)
RT_DEFINE_VEC(real, double)
RT_DEFINE_VEC(real32, float)
RT_DEFINE_VEC(logical, bool)
RT_DEFINE_VEC(complex, rt_cx)

/* a subscript: in range, or the interpreter's message ("in dimension 2" only for a matrix) */
static inline void rt_check_index(int64_t i, int64_t n, int dim, int rank, int line) {
#ifndef BOREAS_RELEASE
    if (i < 1 || i > n) {
        if (rank > 1) rt_fail(line, "index %lld out of bounds 1..%lld in dimension %d", (long long)i, (long long)n, dim);
        rt_fail(line, "index %lld out of bounds 1..%lld", (long long)i, (long long)n);
    }
#else
    (void)i; (void)n; (void)dim; (void)rank; (void)line;
#endif
}

static void rt_shape_text(char *buf, const int64_t *shape, int rank) {
    int k = sprintf(buf, "(");
    for (int d = 0; d < rank; d++) k += sprintf(buf + k, d ? ", %lld" : "%lld", (long long)shape[d]);
    sprintf(buf + k, ")");
}
/* a shape the way a `where` message shows it: [3] or [2, 2] */
static void rt_shape_list(char *buf, const int64_t *shape, int rank) {
    int k = sprintf(buf, "[");
    for (int d = 0; d < rank; d++) k += sprintf(buf + k, d ? ", %lld" : "%lld", (long long)shape[d]);
    sprintf(buf + k, "]");
}
static bool rt_shape_same(const int64_t *a, int ra, const int64_t *b, int rb) {
    if (ra != rb) return false;
    for (int d = 0; d < ra; d++) if (a[d] != b[d]) return false;
    return true;
}
/* two shapes that must be equal: Python orders them as tuples, so the smaller one is named first in an operation */
static void rt_same_shape(const int64_t *a, int ra, const int64_t *b, int rb, int ordered, int line) {
    int same = ra == rb;
    for (int d = 0; same && d < ra; d++) same = a[d] == b[d];
    if (same) return;
    char x[80], y[80];
    if (!ordered) {                                     /* an operation names the two shapes in order of size */
        int less = 0, decided = 0;
        for (int d = 0; !decided && d < ra && d < rb; d++) if (a[d] != b[d]) { less = a[d] < b[d]; decided = 1; }
        if (!decided) less = ra < rb;
        if (!less) { rt_shape_text(x, b, rb); rt_shape_text(y, a, ra); rt_fail(line, "array shape mismatch: %s vs %s", x, y); }
    }
    rt_shape_text(x, a, ra); rt_shape_text(y, b, rb);
    rt_fail(line, "array shape mismatch: %s vs %s", x, y);
}
static void rt_pack_check(const int64_t *a, int ra, const int64_t *m, int rm, int line) {
    int same = ra == rm;
    for (int d = 0; same && d < ra; d++) same = a[d] == m[d];
    if (same) return;
    char x[80], y[80];
    rt_shape_text(x, m, rm); rt_shape_text(y, a, ra);
    rt_fail(line, "pack(): the mask's shape %s differs from the array's %s", x, y);
}
static void rt_init_shape_error(const int64_t *init, int ri, const int64_t *shape, int rs, const char *name, int line) {
    int same = ri == rs;
    for (int d = 0; same && d < ri; d++) same = init[d] == shape[d];
    if (same) return;
    char x[80], y[80];
    rt_shape_text(x, init, ri); rt_shape_text(y, shape, rs);
    rt_fail(line, "initializer has shape %s, '%s' has shape %s", x, name, y);
}

/* an array as text: [1, 2, 3], and a matrix by rows: [[1, 2], [3, 4]] */
#define RT_DEFINE_WRITE(NAME, T, WRITE)                                                                            \
    static void rt_write_##NAME##_arr(const rt_##NAME##_arr *a) {                                                  \
        if (a->rank == 1) {                                                                                        \
            fputc('[', rt_out);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) { if (i) fputs(", ", rt_out); WRITE(a->data[i]); }           \
            fputc(']', rt_out);                                                                                    \
        } else if (a->rank == 2) {                                                                                 \
            fputc('[', rt_out);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) {                                                            \
                if (i) fputs(", ", rt_out);                                                                        \
                fputc('[', rt_out);                                                                                \
                for (int64_t j = 0; j < a->shape[1]; j++) { if (j) fputs(", ", rt_out); WRITE(a->data[i + j * a->shape[0]]); } \
                fputc(']', rt_out);                                                                                \
            }                                                                                                      \
            fputc(']', rt_out);                                                                                    \
        } else {                                                                                                   \
            fputc('[', rt_out);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) {                                                            \
                if (i) fputs(", ", rt_out);                                                                        \
                fputc('[', rt_out);                                                                                \
                for (int64_t j = 0; j < a->shape[1]; j++) {                                                        \
                    if (j) fputs(", ", rt_out);                                                                    \
                    fputc('[', rt_out);                                                                            \
                    for (int64_t k = 0; k < a->shape[2]; k++) {                                                    \
                        if (k) fputs(", ", rt_out);                                                                \
                        WRITE(a->data[i + j * a->shape[0] + k * a->shape[0] * a->shape[1]]);                       \
                    }                                                                                              \
                    fputc(']', rt_out);                                                                            \
                }                                                                                                  \
                fputc(']', rt_out);                                                                                \
            }                                                                                                      \
            fputc(']', rt_out);                                                                                    \
        }                                                                                                          \
    }
/* ---- complex numbers: CPython's own arithmetic (Objects/complexobject.c, the same in 3.10 to 3.13), step by step ---------
   The libm functions it calls go through volatile pointers, so that a compiler cannot work a constant out itself. */
static double (*volatile rt_fp_hypot)(double, double) = hypot;
static double (*volatile rt_fp_pow)(double, double) = pow;
static double (*volatile rt_fp_atan2)(double, double) = atan2;
static double (*volatile rt_fp_exp)(double) = exp;
static double (*volatile rt_fp_log)(double) = log;
/* sin and cos of one argument: CPython's complex functions are compiled with the pair fused into one call (clang: __sincos_stret, whose
   last digit differs from sin() and cos() alone), so the same pair is written here; the argument is opaque so nothing is folded */
static inline void rt_sincos(double x, double *s, double *c) {
    double v = x;
    __asm__ volatile("" : "+m"(v));
    *s = sin(v);
    *c = cos(v);
}
static inline rt_cx rt_cx_mk(double re, double im) { rt_cx r; r.re = re; r.im = im; return r; }
static inline rt_cx rt_cx_add(rt_cx a, rt_cx b) { return rt_cx_mk(a.re + b.re, a.im + b.im); }
static inline rt_cx rt_cx_sub(rt_cx a, rt_cx b) { return rt_cx_mk(a.re - b.re, a.im - b.im); }
static inline rt_cx rt_cx_neg(rt_cx a) { return rt_cx_mk(-a.re, -a.im); }
static inline rt_cx rt_cx_conj(rt_cx a) { return rt_cx_mk(a.re, -a.im); }
static inline rt_cx rt_cx_mul(rt_cx a, rt_cx b) { return rt_cx_mk(a.re * b.re - a.im * b.im, a.re * b.im + a.im * b.re); }
static inline bool rt_cx_eq(rt_cx a, rt_cx b) { return a.re == b.re && a.im == b.im; }
static inline bool rt_cx_eq_real(rt_cx a, double x) { return a.re == x && a.im == 0.0; }
/* complex == whole number: Python compares the real part (a float) with the integer exactly, when the imaginary part is zero */
static bool rt_cx_eq_int(rt_cx a, int64_t n) {
    if (a.im != 0.0) return false;
    double d = a.re;
    if (!isfinite(d) || d != floor(d) || fabs(d) >= 9223372036854775808.0) return false;
    return (int64_t)d == n;
}
/* _Py_c_quot: errno = EDOM when the divisor is zero */
static rt_cx rt_cx_quot(rt_cx a, rt_cx b) {
    rt_cx r;
    const double abs_breal = b.re < 0 ? -b.re : b.re;
    const double abs_bimag = b.im < 0 ? -b.im : b.im;
    if (abs_breal >= abs_bimag) {
        if (abs_breal == 0.0) { errno = EDOM; r.re = r.im = 0.0; }
        else {
            const double ratio = b.im / b.re;
            const double denom = b.re + b.im * ratio;
            r.re = (a.re + a.im * ratio) / denom;
            r.im = (a.im - a.re * ratio) / denom;
        }
    } else if (abs_bimag >= abs_breal) {
        const double ratio = b.re / b.im;
        const double denom = b.re * ratio + b.im;
        r.re = (a.re * ratio + a.im) / denom;
        r.im = (a.im * ratio - a.re) / denom;
    } else {
        r.re = r.im = NAN;
    }
    return r;
}
static rt_cx rt_cx_div(rt_cx a, rt_cx b, int line) {
    errno = 0;
    rt_cx q = rt_cx_quot(a, b);
    if (errno == EDOM) rt_fail(line, "division by zero");
    return q;
}
/* _Py_c_pow */
static rt_cx rt_cx_c_pow(rt_cx a, rt_cx b) {
    rt_cx r;
    double vabs, len, at, phase;
    if (b.re == 0. && b.im == 0.) { r.re = 1.; r.im = 0.; }
    else if (a.re == 0. && a.im == 0.) {
        if (b.im != 0. || b.re < 0.) errno = EDOM;
        r.re = 0.; r.im = 0.;
    } else {
        vabs = rt_fp_hypot(a.re, a.im);
        len = rt_fp_pow(vabs, b.re);
        at = rt_fp_atan2(a.im, a.re);
        phase = at * b.re;
        if (b.im != 0.0) {
            len /= rt_fp_exp(at * b.im);
            phase += b.im * rt_fp_log(vabs);
        }
        double sn, cs;
        rt_sincos(phase, &sn, &cs);
        r.re = len * cs;
        r.im = len * sn;
    }
    return r;
}
static rt_cx rt_cx_powu(rt_cx x, long n) {
    rt_cx r = rt_cx_mk(1.0, 0.0), p = x;
    long mask = 1;
    while (mask > 0 && n >= mask) {
        if (n & mask) r = rt_cx_mul(r, p);
        mask <<= 1;
        p = rt_cx_mul(p, p);
    }
    return r;
}
static rt_cx rt_cx_powi(rt_cx x, long n) {
    if (n > 0) return rt_cx_powu(x, n);
    return rt_cx_quot(rt_cx_mk(1.0, 0.0), rt_cx_powu(x, -n));
}
/* Python's str() of a float (inf, nan, 3.0, 1e+16) and of a complex ((1e+200+0j), 2j), as an error message shows them */
static const char *rt_py_float(double x, char *buf) {
    if (x != x) strcpy(buf, "nan");
    else if (isinf(x)) strcpy(buf, x < 0 ? "-inf" : "inf");
    else rt_real_text(x, buf);
    return buf;
}
static const char *rt_py_float_nodot(double x, char *buf) {            /* (complex shows 3.0 as 3) */
    rt_py_float(x, buf);
    size_t n = strlen(buf);
    if (n > 2 && buf[n - 2] == '.' && buf[n - 1] == '0') buf[n - 2] = 0;
    return buf;
}
static const char *rt_py_complex(rt_cx z, char *buf) {                 /* buf: 120 bytes */
    char a[48], b[48];
    if (z.re == 0.0 && !signbit(z.re)) { snprintf(buf, 120, "%sj", rt_py_float_nodot(z.im, b)); return buf; }
    rt_py_float_nodot(z.re, a);
    rt_py_float_nodot(z.im, b);
    snprintf(buf, 120, "(%s%s%sj)", a, b[0] == '-' ? "" : "+", b);
    return buf;
}
/* an operand in a message: kind 0 a real, 1 a whole number (ia), 2 a complex */
static const char *rt_py_operand(rt_cx z, int kind, int64_t ia, char *buf) {
    if (kind == 2) return rt_py_complex(z, buf);
    if (kind == 1) { snprintf(buf, 120, "%lld", (long long)ia); return buf; }
    return rt_py_float(z.re, buf);
}
/* a ** b the way complex_pow does it, with its errors (and, under `use ieee`, an overflow or a power of zero gives Infinity) */
static rt_cx rt_cx_pow(rt_cx a, rt_cx b, int ka, int kb, int64_t ia, int64_t ib, int line) {
    rt_cx p;
    errno = 0;
    if (b.im == 0.0 && b.re == floor(b.re) && fabs(b.re) <= 100.0) p = rt_cx_powi(a, (long)b.re);
    else p = rt_cx_c_pow(a, b);
    if (p.re == HUGE_VAL || p.re == -HUGE_VAL || p.im == HUGE_VAL || p.im == -HUGE_VAL) { if (errno == 0) errno = ERANGE; }
    else if (errno == ERANGE) errno = 0;
    if (errno == EDOM) {
#ifdef BOREAS_IEEE
        return rt_cx_mk(INFINITY, 0.0);
#else
        rt_fail(line, "division by zero");
#endif
    }
    if (errno == ERANGE) {
#ifdef BOREAS_IEEE
        return rt_cx_mk(INFINITY, 0.0);
#else
        char x[120], y[120];
        rt_fail(line, "%s ** %s is too large", rt_py_operand(a, ka, ia, x), rt_py_operand(b, kb, ib, y));
#endif
    }
    return p;
}
/* abs(z): _Py_c_abs, with its overflow */
static double rt_cx_abs(rt_cx z, int line) {
    double result;
    if (!isfinite(z.re) || !isfinite(z.im)) {
        if (isinf(z.re)) return fabs(z.re);
        if (isinf(z.im)) return fabs(z.im);
        return NAN;
    }
    result = rt_fp_hypot(z.re, z.im);
    if (!isfinite(result)) rt_fail(line, "abs(): absolute value too large");
    return result;
}
/* ---- the complex functions: CPython's cmath (Modules/cmathmodule.c: sqrt, exp, log, log10, sin, cos, tan, asin, acos, atan,
   sinh, cosh, tanh, asinh, acosh, atanh), the same code in 3.10 to 3.13. Each sets rt_cm_errno the way the C99 Annex G notes there
   do: 0, EDOM (a "math domain error") or ERANGE (a "math range error"); the callers turn those into the interpreter's messages.
   Every libm call goes through a volatile pointer (or is exact: sqrt, ldexp, fabs, copysign). ------------------------------- */
static double (*volatile rt_fp_sin)(double) = sin;
static double (*volatile rt_fp_cos)(double) = cos;
static double (*volatile rt_fp_tan)(double) = tan;
static double (*volatile rt_fp_sinh)(double) = sinh;
static double (*volatile rt_fp_cosh)(double) = cosh;
static double (*volatile rt_fp_tanh)(double) = tanh;
static double (*volatile rt_fp_asinh)(double) = asinh;
static double (*volatile rt_fp_log1p)(double) = log1p;
static int rt_cm_errno;
#define RT_CMP 3.14159265358979323846
#define RT_CMP14 0.25*RT_CMP
#define RT_CMP12 0.5*RT_CMP
#define RT_CMP34 0.75*RT_CMP
#define RT_CMINF HUGE_VAL
#define RT_CMN NAN
#define RT_CMU -9.5426319407711027e33
#define RT_CM_E 2.7182818284590452354
#define RT_CM_LN2 0.6931471805599453094
#define RT_CM_LN10 2.302585092994045684
#define RT_CM_LARGE (DBL_MAX / 4.)
#define RT_CM_SQRT_LARGE (sqrt(RT_CM_LARGE))
#define RT_CM_LOG_LARGE (rt_fp_log(RT_CM_LARGE))
#define RT_CM_SQRT_MIN (sqrt(DBL_MIN))
#define RT_CM_SCALE_UP (2 * (DBL_MANT_DIG / 2) + 1)
#define RT_CM_SCALE_DOWN (-(RT_CM_SCALE_UP + 1) / 2)
enum { RT_ST_NINF, RT_ST_NEG, RT_ST_NZERO, RT_ST_PZERO, RT_ST_POS, RT_ST_PINF, RT_ST_NAN };
static int rt_cm_special_type(double d) {
    if (isfinite(d)) {
        if (d != 0) return copysign(1., d) == 1. ? RT_ST_POS : RT_ST_NEG;
        return copysign(1., d) == 1. ? RT_ST_PZERO : RT_ST_NZERO;
    }
    if (isnan(d)) return RT_ST_NAN;
    return copysign(1., d) == 1. ? RT_ST_PINF : RT_ST_NINF;
}
static const rt_cx rt_cm_acos_special[7][7] = {
    {{RT_CMP34, RT_CMINF}, {RT_CMP, RT_CMINF}, {RT_CMP, RT_CMINF}, {RT_CMP, -RT_CMINF}, {RT_CMP, -RT_CMINF}, {RT_CMP34, -RT_CMINF}, {RT_CMN, RT_CMINF}},
    {{RT_CMP12, RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMP12, -RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMP12, RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMP12, 0.}, {RT_CMP12, -0.}, {RT_CMU, RT_CMU}, {RT_CMP12, -RT_CMINF}, {RT_CMP12, RT_CMN}},
    {{RT_CMP12, RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMP12, 0.}, {RT_CMP12, -0.}, {RT_CMU, RT_CMU}, {RT_CMP12, -RT_CMINF}, {RT_CMP12, RT_CMN}},
    {{RT_CMP12, RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMP12, -RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMP14, RT_CMINF}, {0., RT_CMINF}, {0., RT_CMINF}, {0., -RT_CMINF}, {0., -RT_CMINF}, {RT_CMP14, -RT_CMINF}, {RT_CMN, RT_CMINF}},
    {{RT_CMN, RT_CMINF}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, -RT_CMINF}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_acosh_special[7][7] = {
    {{RT_CMINF, -RT_CMP34}, {RT_CMINF, -RT_CMP}, {RT_CMINF, -RT_CMP}, {RT_CMINF, RT_CMP}, {RT_CMINF, RT_CMP}, {RT_CMINF, RT_CMP34}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {0., -RT_CMP12}, {0., RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {0., -RT_CMP12}, {0., RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP14}, {RT_CMINF, -0.}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMINF, 0.}, {RT_CMINF, RT_CMP14}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_asinh_special[7][7] = {
    {{-RT_CMINF, -RT_CMP14}, {-RT_CMINF, -0.}, {-RT_CMINF, -0.}, {-RT_CMINF, 0.}, {-RT_CMINF, 0.}, {-RT_CMINF, RT_CMP14}, {-RT_CMINF, RT_CMN}},
    {{-RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {-RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{-RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {-0., -0.}, {-0., 0.}, {RT_CMU, RT_CMU}, {-RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP14}, {RT_CMINF, -0.}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMINF, 0.}, {RT_CMINF, RT_CMP14}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, -0.}, {RT_CMN, 0.}, {RT_CMN, RT_CMN}, {RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_atanh_special[7][7] = {
    {{-0., -RT_CMP12}, {-0., -RT_CMP12}, {-0., -RT_CMP12}, {-0., RT_CMP12}, {-0., RT_CMP12}, {-0., RT_CMP12}, {-0., RT_CMN}},
    {{-0., -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {-0., RT_CMP12}, {RT_CMN, RT_CMN}},
    {{-0., -RT_CMP12}, {RT_CMU, RT_CMU}, {-0., -0.}, {-0., 0.}, {RT_CMU, RT_CMU}, {-0., RT_CMP12}, {-0., RT_CMN}},
    {{0., -RT_CMP12}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {0., RT_CMP12}, {0., RT_CMN}},
    {{0., -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {0., RT_CMP12}, {RT_CMN, RT_CMN}},
    {{0., -RT_CMP12}, {0., -RT_CMP12}, {0., -RT_CMP12}, {0., RT_CMP12}, {0., RT_CMP12}, {0., RT_CMP12}, {0., RT_CMN}},
    {{0., -RT_CMP12}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {0., RT_CMP12}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_cosh_special[7][7] = {
    {{RT_CMINF, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMINF, 0.}, {RT_CMINF, -0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMN}, {RT_CMINF, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, 0.}, {RT_CMU, RT_CMU}, {1., 0.}, {1., -0.}, {RT_CMU, RT_CMU}, {RT_CMN, 0.}, {RT_CMN, 0.}},
    {{RT_CMN, 0.}, {RT_CMU, RT_CMU}, {1., -0.}, {1., 0.}, {RT_CMU, RT_CMU}, {RT_CMN, 0.}, {RT_CMN, 0.}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMN}, {RT_CMINF, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, 0.}, {RT_CMN, 0.}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_exp_special[7][7] = {
    {{0., 0.}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {0., 0.}, {0., 0.}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {1., -0.}, {1., 0.}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {1., -0.}, {1., 0.}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMN}, {RT_CMINF, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, -0.}, {RT_CMN, 0.}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_log_special[7][7] = {
    {{RT_CMINF, -RT_CMP34}, {RT_CMINF, -RT_CMP}, {RT_CMINF, -RT_CMP}, {RT_CMINF, RT_CMP}, {RT_CMINF, RT_CMP}, {RT_CMINF, RT_CMP34}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {-RT_CMINF, -RT_CMP}, {-RT_CMINF, RT_CMP}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {-RT_CMINF, -0.}, {-RT_CMINF, 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP12}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMP12}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMP14}, {RT_CMINF, -0.}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMINF, 0.}, {RT_CMINF, RT_CMP14}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMINF, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_sinh_special[7][7] = {
    {{RT_CMINF, RT_CMN}, {RT_CMU, RT_CMU}, {-RT_CMINF, -0.}, {-RT_CMINF, 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMN}, {RT_CMINF, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{0., RT_CMN}, {RT_CMU, RT_CMU}, {-0., -0.}, {-0., 0.}, {RT_CMU, RT_CMU}, {0., RT_CMN}, {0., RT_CMN}},
    {{0., RT_CMN}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {0., RT_CMN}, {0., RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMN}, {RT_CMINF, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, -0.}, {RT_CMN, 0.}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_sqrt_special[7][7] = {
    {{RT_CMINF, -RT_CMINF}, {0., -RT_CMINF}, {0., -RT_CMINF}, {0., RT_CMINF}, {0., RT_CMINF}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMINF}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMN}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMINF, -0.}, {RT_CMINF, -0.}, {RT_CMINF, 0.}, {RT_CMINF, 0.}, {RT_CMINF, RT_CMINF}, {RT_CMINF, RT_CMN}},
    {{RT_CMINF, -RT_CMINF}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMINF, RT_CMINF}, {RT_CMN, RT_CMN}}
};
static const rt_cx rt_cm_tanh_special[7][7] = {
    {{-1., 0.}, {RT_CMU, RT_CMU}, {-1., -0.}, {-1., 0.}, {RT_CMU, RT_CMU}, {-1., 0.}, {-1., 0.}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {-0., -0.}, {-0., 0.}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {0., -0.}, {0., 0.}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{RT_CMN, RT_CMN}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMU, RT_CMU}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}},
    {{1., 0.}, {RT_CMU, RT_CMU}, {1., -0.}, {1., 0.}, {RT_CMU, RT_CMU}, {1., 0.}, {1., 0.}},
    {{RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, -0.}, {RT_CMN, 0.}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}, {RT_CMN, RT_CMN}}
};

#define RT_CM_SPECIAL(z, table) \
    if (!isfinite((z).re) || !isfinite((z).im)) { rt_cm_errno = 0; return table[rt_cm_special_type((z).re)][rt_cm_special_type((z).im)]; }
static rt_cx rt_cm_sqrt(rt_cx z) {
    rt_cx r; double s, d, ax, ay;
    RT_CM_SPECIAL(z, rt_cm_sqrt_special);
    if (z.re == 0. && z.im == 0.) { r.re = 0.; r.im = z.im; return r; }
    ax = fabs(z.re); ay = fabs(z.im);
    if (ax < DBL_MIN && ay < DBL_MIN) {
        ax = ldexp(ax, RT_CM_SCALE_UP);
        s = ldexp(sqrt(ax + rt_fp_hypot(ax, ldexp(ay, RT_CM_SCALE_UP))), RT_CM_SCALE_DOWN);
    } else {
        ax /= 8.;
        s = 2. * sqrt(ax + rt_fp_hypot(ax, ay / 8.));
    }
    d = ay / (2. * s);
    if (z.re >= 0.) { r.re = s; r.im = copysign(d, z.im); }
    else { r.re = d; r.im = copysign(s, z.im); }
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_acos(rt_cx z) {
    rt_cx s1, s2, r;
    RT_CM_SPECIAL(z, rt_cm_acos_special);
    if (fabs(z.re) > RT_CM_LARGE || fabs(z.im) > RT_CM_LARGE) {
        r.re = rt_fp_atan2(fabs(z.im), z.re);
        if (z.re < 0.) r.im = -copysign(rt_fp_log(rt_fp_hypot(z.re / 2., z.im / 2.)) + RT_CM_LN2 * 2., z.im);
        else r.im = copysign(rt_fp_log(rt_fp_hypot(z.re / 2., z.im / 2.)) + RT_CM_LN2 * 2., -z.im);
    } else {
        s1.re = 1. - z.re; s1.im = -z.im;
        s1 = rt_cm_sqrt(s1);
        s2.re = 1. + z.re; s2.im = z.im;
        s2 = rt_cm_sqrt(s2);
        r.re = 2. * rt_fp_atan2(s1.re, s2.re);
        r.im = rt_fp_asinh(s2.re * s1.im - s2.im * s1.re);
    }
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_acosh(rt_cx z) {
    rt_cx s1, s2, r;
    RT_CM_SPECIAL(z, rt_cm_acosh_special);
    if (fabs(z.re) > RT_CM_LARGE || fabs(z.im) > RT_CM_LARGE) {
        r.re = rt_fp_log(rt_fp_hypot(z.re / 2., z.im / 2.)) + RT_CM_LN2 * 2.;
        r.im = rt_fp_atan2(z.im, z.re);
    } else {
        s1.re = z.re - 1.; s1.im = z.im;
        s1 = rt_cm_sqrt(s1);
        s2.re = z.re + 1.; s2.im = z.im;
        s2 = rt_cm_sqrt(s2);
        r.re = rt_fp_asinh(s1.re * s2.re + s1.im * s2.im);
        r.im = 2. * rt_fp_atan2(s1.im, s2.re);
    }
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_asinh(rt_cx z) {
    rt_cx s1, s2, r;
    RT_CM_SPECIAL(z, rt_cm_asinh_special);
    if (fabs(z.re) > RT_CM_LARGE || fabs(z.im) > RT_CM_LARGE) {
        if (z.im >= 0.) r.re = copysign(rt_fp_log(rt_fp_hypot(z.re / 2., z.im / 2.)) + RT_CM_LN2 * 2., z.re);
        else r.re = -copysign(rt_fp_log(rt_fp_hypot(z.re / 2., z.im / 2.)) + RT_CM_LN2 * 2., -z.re);
        r.im = rt_fp_atan2(z.im, fabs(z.re));
    } else {
        s1.re = 1. + z.im; s1.im = -z.re;
        s1 = rt_cm_sqrt(s1);
        s2.re = 1. - z.im; s2.im = z.re;
        s2 = rt_cm_sqrt(s2);
        r.re = rt_fp_asinh(s1.re * s2.im - s2.re * s1.im);
        r.im = rt_fp_atan2(z.im, s1.re * s2.re - s1.im * s2.im);
    }
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_asin(rt_cx z) {
    rt_cx s, r;
    s.re = -z.im; s.im = z.re;
    s = rt_cm_asinh(s);
    r.re = s.im; r.im = -s.re;
    return r;
}
static rt_cx rt_cm_atanh(rt_cx z) {
    rt_cx r; double ay, h;
    RT_CM_SPECIAL(z, rt_cm_atanh_special);
    if (z.re < 0.) return rt_cx_neg(rt_cm_atanh(rt_cx_neg(z)));
    ay = fabs(z.im);
    if (z.re > RT_CM_SQRT_LARGE || ay > RT_CM_SQRT_LARGE) {
        h = rt_fp_hypot(z.re / 2., z.im / 2.);
        r.re = z.re / 4. / h / h;
        r.im = -copysign(RT_CMP / 2., -z.im);
        rt_cm_errno = 0;
    } else if (z.re == 1. && ay < RT_CM_SQRT_MIN) {
        if (ay == 0.) { r.re = HUGE_VAL; r.im = z.im; rt_cm_errno = EDOM; }
        else {
            r.re = -rt_fp_log(sqrt(ay) / sqrt(rt_fp_hypot(ay, 2.)));
            r.im = copysign(rt_fp_atan2(2., -ay) / 2, z.im);
            rt_cm_errno = 0;
        }
    } else {
        r.re = rt_fp_log1p(4. * z.re / ((1 - z.re) * (1 - z.re) + ay * ay)) / 4.;
        r.im = -rt_fp_atan2(-2. * z.im, (1 - z.re) * (1 + z.re) - ay * ay) / 2.;
        rt_cm_errno = 0;
    }
    return r;
}
static rt_cx rt_cm_atan(rt_cx z) {
    rt_cx s, r;
    s.re = -z.im; s.im = z.re;
    s = rt_cm_atanh(s);
    r.re = s.im; r.im = -s.re;
    return r;
}
static rt_cx rt_cm_cosh(rt_cx z) {
    rt_cx r; double x_minus_one, sn, cs;
    if (!isfinite(z.re) || !isfinite(z.im)) {
        if (isinf(z.re) && isfinite(z.im) && z.im != 0.) {
            rt_sincos(z.im, &sn, &cs);
            if (z.re > 0) { r.re = copysign(HUGE_VAL, cs); r.im = copysign(HUGE_VAL, sn); }
            else { r.re = copysign(HUGE_VAL, cs); r.im = -copysign(HUGE_VAL, sn); }
        } else r = rt_cm_cosh_special[rt_cm_special_type(z.re)][rt_cm_special_type(z.im)];
        rt_cm_errno = (isinf(z.im) && !isnan(z.re)) ? EDOM : 0;
        return r;
    }
    rt_sincos(z.im, &sn, &cs);
    if (fabs(z.re) > RT_CM_LOG_LARGE) {
        x_minus_one = z.re - copysign(1., z.re);
        r.re = cs * rt_fp_cosh(x_minus_one) * RT_CM_E;
        r.im = sn * rt_fp_sinh(x_minus_one) * RT_CM_E;
    } else {
        r.re = cs * rt_fp_cosh(z.re);
        r.im = sn * rt_fp_sinh(z.re);
    }
    rt_cm_errno = (isinf(r.re) || isinf(r.im)) ? ERANGE : 0;
    return r;
}
static rt_cx rt_cm_cos(rt_cx z) {
    rt_cx r;
    r.re = -z.im; r.im = z.re;
    return rt_cm_cosh(r);
}
static rt_cx rt_cm_exp(rt_cx z) {
    rt_cx r; double l, sn, cs;
    if (!isfinite(z.re) || !isfinite(z.im)) {
        if (isinf(z.re) && isfinite(z.im) && z.im != 0.) {
            rt_sincos(z.im, &sn, &cs);
            if (z.re > 0) { r.re = copysign(HUGE_VAL, cs); r.im = copysign(HUGE_VAL, sn); }
            else { r.re = copysign(0., cs); r.im = copysign(0., sn); }
        } else r = rt_cm_exp_special[rt_cm_special_type(z.re)][rt_cm_special_type(z.im)];
        rt_cm_errno = (isinf(z.im) && (isfinite(z.re) || (isinf(z.re) && z.re > 0))) ? EDOM : 0;
        return r;
    }
    rt_sincos(z.im, &sn, &cs);
    if (z.re > RT_CM_LOG_LARGE) {
        l = rt_fp_exp(z.re - 1.);
        r.re = l * cs * RT_CM_E;
        r.im = l * sn * RT_CM_E;
    } else {
        l = rt_fp_exp(z.re);
        r.re = l * cs;
        r.im = l * sn;
    }
    rt_cm_errno = (isinf(r.re) || isinf(r.im)) ? ERANGE : 0;
    return r;
}
static rt_cx rt_cm_log(rt_cx z) {
    rt_cx r; double ax, ay, am, an, h;
    RT_CM_SPECIAL(z, rt_cm_log_special);
    ax = fabs(z.re); ay = fabs(z.im);
    if (ax > RT_CM_LARGE || ay > RT_CM_LARGE) {
        r.re = rt_fp_log(rt_fp_hypot(ax / 2., ay / 2.)) + RT_CM_LN2;
    } else if (ax < DBL_MIN && ay < DBL_MIN) {
        if (ax > 0. || ay > 0.) {
            r.re = rt_fp_log(rt_fp_hypot(ldexp(ax, DBL_MANT_DIG), ldexp(ay, DBL_MANT_DIG))) - DBL_MANT_DIG * RT_CM_LN2;
        } else {
            r.re = -HUGE_VAL;
            r.im = rt_fp_atan2(z.im, z.re);
            rt_cm_errno = EDOM;
            return r;
        }
    } else {
        h = rt_fp_hypot(ax, ay);
        if (0.71 <= h && h <= 1.73) {
            am = ax > ay ? ax : ay;
            an = ax > ay ? ay : ax;
            r.re = rt_fp_log1p((am - 1) * (am + 1) + an * an) / 2.;
        } else r.re = rt_fp_log(h);
    }
    r.im = rt_fp_atan2(z.im, z.re);
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_log10(rt_cx z) {
    rt_cx r = rt_cm_log(z);
    int save = rt_cm_errno;
    r.re = r.re / RT_CM_LN10;
    r.im = r.im / RT_CM_LN10;
    rt_cm_errno = save;
    return r;
}
static rt_cx rt_cm_sinh(rt_cx z) {
    rt_cx r; double x_minus_one, sn, cs;
    if (!isfinite(z.re) || !isfinite(z.im)) {
        if (isinf(z.re) && isfinite(z.im) && z.im != 0.) {
            rt_sincos(z.im, &sn, &cs);
            if (z.re > 0) { r.re = copysign(HUGE_VAL, cs); r.im = copysign(HUGE_VAL, sn); }
            else { r.re = -copysign(HUGE_VAL, cs); r.im = copysign(HUGE_VAL, sn); }
        } else r = rt_cm_sinh_special[rt_cm_special_type(z.re)][rt_cm_special_type(z.im)];
        rt_cm_errno = (isinf(z.im) && !isnan(z.re)) ? EDOM : 0;
        return r;
    }
    rt_sincos(z.im, &sn, &cs);
    if (fabs(z.re) > RT_CM_LOG_LARGE) {
        x_minus_one = z.re - copysign(1., z.re);
        r.re = cs * rt_fp_sinh(x_minus_one) * RT_CM_E;
        r.im = sn * rt_fp_cosh(x_minus_one) * RT_CM_E;
    } else {
        r.re = cs * rt_fp_sinh(z.re);
        r.im = sn * rt_fp_cosh(z.re);
    }
    rt_cm_errno = (isinf(r.re) || isinf(r.im)) ? ERANGE : 0;
    return r;
}
static rt_cx rt_cm_sin(rt_cx z) {
    rt_cx s, r;
    s.re = -z.im; s.im = z.re;
    s = rt_cm_sinh(s);
    r.re = s.im; r.im = -s.re;
    return r;
}
static rt_cx rt_cm_tanh(rt_cx z) {
    rt_cx r; double tx, ty, cx, txty, denom, sn, cs;
    if (!isfinite(z.re) || !isfinite(z.im)) {
        if (isinf(z.re) && isfinite(z.im) && z.im != 0.) {
            rt_sincos(z.im, &sn, &cs);
            if (z.re > 0) { r.re = 1.0; r.im = copysign(0., 2. * sn * cs); }
            else { r.re = -1.0; r.im = copysign(0., 2. * sn * cs); }
        } else r = rt_cm_tanh_special[rt_cm_special_type(z.re)][rt_cm_special_type(z.im)];
        rt_cm_errno = (isinf(z.im) && isfinite(z.re)) ? EDOM : 0;
        return r;
    }
    if (fabs(z.re) > RT_CM_LOG_LARGE) {
        rt_sincos(z.im, &sn, &cs);
        r.re = copysign(1., z.re);
        r.im = 4. * sn * cs * rt_fp_exp(-2. * fabs(z.re));
    } else {
        tx = rt_fp_tanh(z.re);
        ty = rt_fp_tan(z.im);
        cx = 1. / rt_fp_cosh(z.re);
        txty = tx * ty;
        denom = 1. + txty * txty;
        r.re = tx * (1. + ty * ty) / denom;
        r.im = ((ty / denom) * cx) * cx;
    }
    rt_cm_errno = 0;
    return r;
}
static rt_cx rt_cm_tan(rt_cx z) {
    rt_cx s, r;
    s.re = -z.im; s.im = z.re;
    s = rt_cm_tanh(s);
    r.re = s.im; r.im = -s.re;
    return r;
}
#undef RT_CM_SPECIAL
/* the function called `fn` (0..15, in the order of RT_CM_NAMES) of z; a domain or range error ends the program with the interpreter's message
   (Python raises there whether or not the file says `use ieee`) */
static const char *const rt_cm_names[16] = {"sqrt", "exp", "log", "log10", "sin", "cos", "tan", "asin", "acos", "atan", "sinh", "cosh", "tanh", "asinh", "acosh", "atanh"};
static rt_cx rt_cm_call(int fn, rt_cx z, int line) {
    rt_cx r;
    rt_cm_errno = 0;
    switch (fn) {
    case 0: r = rt_cm_sqrt(z); break;   case 1: r = rt_cm_exp(z); break;   case 2: r = rt_cm_log(z); break;   case 3: r = rt_cm_log10(z); break;
    case 4: r = rt_cm_sin(z); break;    case 5: r = rt_cm_cos(z); break;   case 6: r = rt_cm_tan(z); break;    case 7: r = rt_cm_asin(z); break;
    case 8: r = rt_cm_acos(z); break;   case 9: r = rt_cm_atan(z); break;  case 10: r = rt_cm_sinh(z); break;  case 11: r = rt_cm_cosh(z); break;
    case 12: r = rt_cm_tanh(z); break;  case 13: r = rt_cm_asinh(z); break; case 14: r = rt_cm_acosh(z); break; default: r = rt_cm_atanh(z); break;
    }
    if (rt_cm_errno == EDOM || rt_cm_errno == ERANGE) {
        char b[120];
        const char *shown = rt_shown_override ? rt_shown_override : rt_py_complex(z, b);
        if (rt_cm_errno == EDOM) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", rt_cm_names[fn], shown);
        rt_fail(line, "%s(%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", rt_cm_names[fn], shown);
    }
    return r;
}
static void rt_write_complex(rt_cx z) { char a[48], b[48]; fprintf(rt_out, "(%s, %s)", rt_py_float(z.re, a), rt_py_float(z.im, b)); }

RT_DEFINE_WRITE(int, int64_t, rt_write_int)
RT_DEFINE_WRITE(int32, int32_t, rt_write_int)
RT_DEFINE_WRITE(real, double, rt_write_real)
RT_DEFINE_WRITE(real32, float, rt_write_real32)
RT_DEFINE_WRITE(logical, bool, rt_write_bool)
RT_DEFINE_WRITE(complex, rt_cx, rt_write_complex)

/* sum and product of whole numbers are worked out exactly and checked once at the end, as the interpreter does */
static inline void rt_check_range(rt_i128 v, int64_t lo, int64_t hi, const char *name, int bits, int line) {
#ifndef BOREAS_RELEASE
    if (v < lo || v > hi) {
        char b[64];
        if (bits == 1) rt_fail(line, "%s(): integer overflow (%s does not fit in 64 bits)", name, rt_i128_text(v, b));
        rt_fail(line, "%s(): %s does not fit in int32 (32 bits)", name, rt_i128_text(v, b));
    }
#else
    (void)v; (void)lo; (void)hi; (void)name; (void)bits; (void)line;
#endif
}

static void rt_set_shown(char *buf, const int64_t *shape, int rank) {
    char sh[80];
    rt_shape_text(sh, shape, rank);
    sprintf(buf, "an array of shape %s", sh);
    rt_shown_override = buf;
}

static void rt_dot_shapes(const int64_t *a, const int64_t *b, int line) {
    if (a[0] != b[0]) rt_fail(line, "dot_product(): shapes differ ((%lld) vs (%lld))", (long long)a[0], (long long)b[0]);
}
static void rt_matmul_check(int64_t inner_a, int64_t inner_b, int line) {
    if (inner_a != inner_b) rt_fail(line, "matmul(): inner dimensions differ (%lld vs %lld)", (long long)inner_a, (long long)inner_b);
}

/* ---- real32: single precision, worked out as the interpreter does -------------------------------------------------- */

/* the nearest single-precision value to a double; a finite number too large for real32 stops the program (unless
   `use ieee`), and the message shows the double as Python does */
static inline float rt_r32(double d, int line) {
    float f = (float)d;
#ifndef BOREAS_IEEE
    if (isinf(f) && !isinf(d)) { char b[48]; rt_fail(line, "%s is too large for real32", rt_real_text(d, b)); }
#else
    (void)line;
#endif
    return f;
}
/* a library function of one real32 (sinf, expf, ...): no real answer, or too large, stops the program unless `use ieee` */
static inline float rt_math1f(const char *name, float r, float x, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (r != r && isfinite(x)) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown((double)x, 0, b));
    if (isinf(r) && isfinite(x)) rt_fail(line, "%s(%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", name, rt_shown((double)x, 0, b));
#else
    (void)name; (void)x; (void)line;
#endif
    return r;
}
static inline float rt_logf(float x, float (*fn)(float), const char *name, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (x <= 0.0f && isfinite(x)) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown((double)x, 0, b));
#else
    (void)name; (void)line;
#endif
    return fn(x);
}
/* real32 ** whole number, as gfortran does it: repeated squaring with every product rounded to single */
static float rt_spow_i(float a, int64_t b, int line) {
    if (b == 0) return 1.0f;
    uint64_t n = b < 0 ? 0 - (uint64_t)b : (uint64_t)b;
    double x = a, y = (n % 2) ? (double)a : 1.0;
    while (n >> 1) {
        n >>= 1;
        x = rt_r32(x * x, line);
        if (n % 2) y = rt_r32(y * x, line);
    }
    if (b < 0) {
        if (y == 0.0) {
#ifdef BOREAS_IEEE
            return INFINITY;
#else
            rt_fail(line, "division by zero");
#endif
        }
        return rt_r32(1.0 / y, line);
    }
    return (float)y;
}
/* real32 ** real32: the C library's powf */
static float rt_spow_f(float a, float b, int line) {
    float r = powf(a, b);
#ifndef BOREAS_IEEE
    if (!(r == r && !isinf(r)) && isfinite(a) && isfinite(b)) {
        char x[48], y[48];
        rt_fail(line, "%s ** %s is %s", rt_real_text((double)a, x), rt_real_text((double)b, y), r != r ? "not a real number" : "too large");
    }
#else
    (void)line;
#endif
    return r;
}

/* ---- allocatable arrays: an array whose data is NULL is not allocated (these checks stay in --release) ---------- */
static inline void rt_need_alloc(const void *data, const char *name, int line) {
    if (!data) rt_fail(line, "'%s' is not allocated; use 'allocate %s(...)' first", name, name);
}
static inline void rt_check_extent(int64_t n, int line) {
    if (n < 0) rt_fail(line, "array extents must be non-negative integers");
}

/* ---- sections: a[lo:hi:step, i, :] ---------------------------------------------------------------------------- */
/* checked as the interpreter does, one dimension at a time: the step, the ends, then the bounds (an empty section is never out of bounds) */
static inline void rt_check_step_nonzero(int64_t step, int line) { if (step == 0) rt_fail(line, "the step of a section cannot be zero"); }
static inline void rt_check_backwards(int64_t step, const char *name, int64_t n, int line) {
    if (step < 0) rt_fail(line, "a section with a negative step needs both ends: write %s[%lld:1:%lld] to run backwards", name, (long long)n, (long long)step);
}
static inline int64_t rt_slice_count(int64_t lo, int64_t hi, int64_t step, int64_t n, int dim, int rank, int has_step, int line) {
    int nonempty = step > 0 ? lo <= hi : lo >= hi;
    if (!nonempty) return 0;
    if (lo < 1 || lo > n || hi < 1 || hi > n) {
        char t[80], where[24] = "";
        if (has_step) snprintf(t, sizeof t, "%lld:%lld:%lld", (long long)lo, (long long)hi, (long long)step);
        else snprintf(t, sizeof t, "%lld:%lld", (long long)lo, (long long)hi);
        if (rank > 1) snprintf(where, sizeof where, " in dimension %d", dim);
        rt_fail(line, "slice %s out of bounds 1..%lld%s", t, (long long)n, where);
    }
    return step > 0 ? (hi - lo) / step + 1 : (lo - hi) / (0 - step) + 1;
}

/* ---- more of the math functions: whole numbers from reals, sign, modulo, dim, conversions ------------------------ */
static inline void rt_no_whole(const char *name, int line) {
    rt_fail(line, "%s(): a NaN or an infinite value has no whole number", name);
}
/* floor (op 0), ceiling (1), nint (2) of a real; the answer is an int (bits 64) or, for a real32, an int32 (bits 32) */
static inline int64_t rt_whole(double x, int op, int bits, int line) {
    static const char *names[] = {"floor", "ceiling", "nint"};
    if (x - x != 0) rt_no_whole(names[op], line);
    double n = op == 0 ? floor(x) : op == 1 ? ceil(x) : copysign(floor(fabs(x) + 0.5), x);
    if (bits == 32) {
        if (n < -2147483648.0 || n > 2147483647.0) rt_fail(line, "%s(): %.0f does not fit in int32 (32 bits)", names[op], n);
    } else if (n >= 9223372036854775808.0 || n < -9223372036854775808.0) {
        rt_fail(line, "%s(): %.0f does not fit in 64 bits", names[op], n);
    }
    return (int64_t)n;
}
static inline void rt_cannot_convert(const char *name, double x, int line) {
    rt_fail(line, "%s(): cannot convert float %s to integer", name, x != x ? "NaN" : "infinity");
}
static inline double rt_aint(double x, int line) { if (x - x != 0) rt_cannot_convert("aint", x, line); return trunc(x); }
static inline double rt_anint(double x, int line) { if (x - x != 0) rt_cannot_convert("anint", x, line); return copysign(floor(fabs(x) + 0.5), x); }
/* int32(x) and int64(x) of a real: truncated, and checked to fit */
static inline int64_t rt_to_whole(double x, int bits, const char *name, int line) {
    if (x - x != 0) rt_cannot_convert(name, x, line);
    double t = trunc(x);
    if (bits == 32) {
        if (t < -2147483648.0 || t > 2147483647.0) rt_fail(line, "int32(): %.0f does not fit in int32 (32 bits)", t);
    } else if (t >= 9223372036854775808.0 || t < -9223372036854775808.0) {
        rt_fail(line, "int(): %.0f does not fit in int (64 bits)", t);
    }
    return (int64_t)t;
}
static inline double rt_atanh(double x, int was_int, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (fabs(x) >= 1.0) rt_fail(line, "atanh(%s) has no real answer (a file that says `use ieee` gets NaN instead)", rt_shown(x, was_int, b));
#else
    (void)was_int; (void)line;
#endif
    return atanh(x);
}
static inline float rt_atanhf(float x, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (fabsf(x) >= 1.0f && isfinite(x)) rt_fail(line, "atanh(%s) has no real answer (a file that says `use ieee` gets NaN instead)", rt_shown((double)x, 0, b));
#else
    (void)line;
#endif
    return atanhf(x);
}
static inline int64_t rt_sign_int(int64_t a, int64_t b, int line) {
    int64_t m = a < 0 ? -a : a;
    if (a == INT64_MIN) { char t[48]; rt_fail(line, "sign(): integer overflow (%s does not fit in 64 bits)", rt_i128_text(-(rt_i128)a, t)); }
    return b < 0 ? -m : m;
}
static inline int32_t rt_sign_int32(int32_t a, int32_t b, int line) {
    if (a == INT32_MIN) rt_fail(line, "sign(): 2147483648 does not fit in int32 (32 bits)");
    int32_t m = a < 0 ? -a : a;
    return b < 0 ? -m : m;
}
static inline int64_t rt_modulo(int64_t a, int64_t b, int line) {
    if (b == 0) rt_fail(line, "modulo(%lld, %lld): division by zero", (long long)a, (long long)b);
    if (b == -1) return 0;
    int64_t r = a % b;
    return (r != 0 && ((r < 0) != (b < 0))) ? r + b : r;
}
static inline double rt_fmodulo(double a, double b, int a_int, int b_int, int line) {
    char x[48], y[48];
    if (b == 0.0) rt_fail(line, "modulo(%s, %s): division by zero", rt_shown(a, a_int, x), rt_shown(b, b_int, y));
    double q = a / b;
    if (q - q != 0) rt_fail(line, "modulo(%s, %s): division by zero", rt_shown(a, a_int, x), rt_shown(b, b_int, y));     /* (the interpreter words every failure of modulo so) */
    return a - floor(q) * b;
}
static inline int64_t rt_dim_int(int64_t a, int64_t b, int bits, int line) {
    rt_i128 d = (rt_i128)a - b;
    if (d <= 0) return 0;
    if (bits == 32 ? d > INT32_MAX : d > INT64_MAX) {
        char t[48];
        if (bits == 32) rt_fail(line, "dim(): %s does not fit in int32 (32 bits)", rt_i128_text(d, t));
        rt_fail(line, "dim(): integer overflow (%s does not fit in 64 bits)", rt_i128_text(d, t));
    }
    return (int64_t)d;
}
static inline double rt_dim_real(double a, double b) { double d = a - b; return 0.0 > d ? 0.0 : d; }

/* ---- array functions ------------------------------------------------------------------------------------------- */
static inline void rt_reshape_check(const int64_t *want, int rank, int64_t have, int line) {
    int64_t prod = 1;
    for (int d = 0; d < rank; d++) if (want[d] < 0) rt_fail(line, "reshape(): shape must be non-negative integers");
    for (int d = 0; d < rank; d++) prod *= want[d];
    if (prod != have) {
        char t[80];
        rt_shape_text(t, want, rank);
        rt_fail(line, "reshape(): %lld elements cannot fill shape %s", (long long)have, t);
    }
}
static inline void rt_check_nonempty(int64_t n, const char *name, int line) {
    if (n == 0) rt_fail(line, "%s(): the array is empty", name);
}
static inline int64_t rt_cshift_source(int64_t p, int64_t shift, int64_t n, int64_t stride) {
    int64_t c = (p / stride) % n;
    int64_t s = ((shift % n) + n) % n;
    return p + (((c + s) % n) - c) * stride;
}
/* three arrays that must share a shape (merge): the message names the two smallest different shapes, as the interpreter does */
static int rt_shape_less(const int64_t *a, int ra, const int64_t *b, int rb) {
    for (int d = 0; d < ra && d < rb; d++) if (a[d] != b[d]) return a[d] < b[d];
    return ra < rb;
}
static int rt_shape_equal(const int64_t *a, int ra, const int64_t *b, int rb) {
    if (ra != rb) return 0;
    for (int d = 0; d < ra; d++) if (a[d] != b[d]) return 0;
    return 1;
}
static void rt_same_shape3(const int64_t *a, int ra, const int64_t *b, int rb, const int64_t *c, int rc, int line) {
    if (rt_shape_equal(a, ra, b, rb) && rt_shape_equal(a, ra, c, rc)) return;
    const int64_t *s[3] = {a, b, c};
    int r[3] = {ra, rb, rc};
    int lo = 0;
    for (int i = 1; i < 3; i++) if (rt_shape_less(s[i], r[i], s[lo], r[lo])) lo = i;
    int next = -1;
    for (int i = 0; i < 3; i++)
        if (!rt_shape_equal(s[i], r[i], s[lo], r[lo]) && (next < 0 || rt_shape_less(s[i], r[i], s[next], r[next]))) next = i;
    char x[80], y[80];
    rt_shape_text(x, s[lo], r[lo]); rt_shape_text(y, s[next], r[next]);
    rt_fail(line, "array shape mismatch: %s vs %s", x, y);
}

/* ---- strings: ASCII text only (the compiler refuses anything else), NUL-terminated. A variable or a temporary owns
   its buffer; a literal is a view that is never freed. Storing a string copies it. ---- */
typedef struct { char *p; int64_t n; } rt_str;
#define RT_LIT(s) ((rt_str){(char *)(s), (int64_t)(sizeof(s) - 1)})
static inline const char *rt_cs(rt_str s) { return s.p ? s.p : ""; }
/* to_integer, to_real, to_logical: the number or word the text spells (blanks around it are ignored), or nothing. */
static bool rt_blank(char c) { return c == ' ' || (c >= 9 && c <= 13) || (c >= 28 && c <= 31); }
static void rt_strip(rt_str s, const char **b, int64_t *n) {
    const char *p = s.p ? s.p : ""; int64_t k = s.n;
    while (k > 0 && rt_blank(p[0])) { p++; k--; }
    while (k > 0 && rt_blank(p[k - 1])) k--;
    *b = p; *n = k;
}
static bool rt_parse_int(rt_str s, int64_t *out) {
    const char *p; int64_t n; rt_strip(s, &p, &n);
    int64_t i = 0; bool neg = false;
    if (n > 0 && (p[0] == '+' || p[0] == '-')) { neg = p[0] == '-'; i = 1; }
    if (i >= n) return false;
    uint64_t v = 0, lim = neg ? 9223372036854775808ULL : 9223372036854775807ULL;
    for (; i < n; i++) {
        if (p[i] < '0' || p[i] > '9') return false;
        unsigned d = (unsigned)(p[i] - '0');
        if (v > (lim - d) / 10) return false;              /* does not fit in an int */
        v = v * 10 + d;
    }
    *out = neg ? (int64_t)(0 - v) : (int64_t)v;
    return true;
}
static bool rt_parse_real(rt_str s, double *out) {
    const char *p; int64_t n; rt_strip(s, &p, &n);
    int64_t i = 0, a = 0, b = 0;
    if (n > 0 && (p[0] == '+' || p[0] == '-')) i = 1;
    while (i < n && p[i] >= '0' && p[i] <= '9') { i++; a++; }
    if (i < n && p[i] == '.') { i++; while (i < n && p[i] >= '0' && p[i] <= '9') { i++; b++; } }
    if (a == 0 && b == 0) return false;
    if (i < n && (p[i] == 'e' || p[i] == 'E' || p[i] == 'd' || p[i] == 'D')) {
        i++;
        if (i < n && (p[i] == '+' || p[i] == '-')) i++;
        int64_t e = 0;
        while (i < n && p[i] >= '0' && p[i] <= '9') { i++; e++; }
        if (e == 0) return false;
    }
    if (i != n) return false;
    char small[128], *buf = n < 128 ? small : (char *)malloc((size_t)n + 1);
    if (!buf) rt_fail(0, "out of memory");
    for (int64_t k = 0; k < n; k++) buf[k] = (p[k] == 'd' || p[k] == 'D') ? 'e' : p[k];
    buf[n] = 0;
    *out = strtod(buf, NULL);
    if (buf != small) free(buf);
    return true;
}
static bool rt_parse_logical(rt_str s, bool *out) {
    const char *p; int64_t n; rt_strip(s, &p, &n);
    char w[8];
    if (n < 1 || n > 7) return false;
    for (int64_t k = 0; k < n; k++) w[k] = (p[k] >= 'A' && p[k] <= 'Z') ? (char)(p[k] + 32) : p[k];
    w[n] = 0;
    if (!strcmp(w, "t") || !strcmp(w, ".t.") || !strcmp(w, "true") || !strcmp(w, ".true.")) { *out = true; return true; }
    if (!strcmp(w, "f") || !strcmp(w, ".f.") || !strcmp(w, "false") || !strcmp(w, ".false.")) { *out = false; return true; }
    return false;
}
static inline void rt_free_str(rt_str *s) { free(s->p); s->p = NULL; s->n = 0; }
static rt_str rt_str_new(const char *p, int64_t n, int line) {
    rt_str r;
    r.p = (char *)malloc((size_t)n + 1);
    if (!r.p) rt_fail(line, "out of memory");
    if (n > 0) memcpy(r.p, p, (size_t)n);
    r.p[n] = 0; r.n = n;
    return r;
}
static void rt_str_set(rt_str *dst, rt_str src, int line) {
    rt_str c = rt_str_new(rt_cs(src), src.n, line);
    free(dst->p);
    *dst = c;
}
static rt_str rt_str_cat(rt_str a, rt_str b, int line) {
    rt_str r;
    r.p = (char *)malloc((size_t)(a.n + b.n) + 1);
    if (!r.p) rt_fail(line, "out of memory");
    if (a.n > 0) memcpy(r.p, a.p, (size_t)a.n);
    if (b.n > 0) memcpy(r.p + a.n, b.p, (size_t)b.n);
    r.n = a.n + b.n; r.p[r.n] = 0;
    return r;
}
/* the ordering of Python's strings: by code point, then by length (bytes, for ASCII) */
static int rt_str_cmp(rt_str a, rt_str b) {
    int64_t m = a.n < b.n ? a.n : b.n;
    int c = m > 0 ? memcmp(a.p, b.p, (size_t)m) : 0;
    if (c) return c < 0 ? -1 : 1;
    return a.n < b.n ? -1 : a.n > b.n ? 1 : 0;
}
static void rt_write_string(rt_str s) { fputs(rt_cs(s), rt_out); }
static rt_str rt_str_upper(rt_str s, int line) {
    rt_str r = rt_str_new(rt_cs(s), s.n, line);
    for (int64_t i = 0; i < r.n; i++) if (r.p[i] >= 'a' && r.p[i] <= 'z') r.p[i] -= 32;
    return r;
}
static rt_str rt_str_lower(rt_str s, int line) {
    rt_str r = rt_str_new(rt_cs(s), s.n, line);
    for (int64_t i = 0; i < r.n; i++) if (r.p[i] >= 'A' && r.p[i] <= 'Z') r.p[i] += 32;
    return r;
}
static int64_t rt_len_trim(rt_str s) {
    int64_t n = s.n;
    while (n > 0 && s.p[n - 1] == ' ') n--;
    return n;
}
static rt_str rt_str_trim(rt_str s, int line) { return rt_str_new(rt_cs(s), rt_len_trim(s), line); }
static rt_str rt_str_repeat(rt_str s, int64_t k, int line) {
    if (k < 0) rt_fail(line, "repeat(): the count must be a whole number of 0 or more, got %lld", (long long)k);
    rt_str r;
    r.p = (char *)malloc((size_t)(s.n * k) + 1);
    if (!r.p) rt_fail(line, "out of memory");
    for (int64_t i = 0; i < k; i++) if (s.n > 0) memcpy(r.p + i * s.n, s.p, (size_t)s.n);
    r.n = s.n * k; r.p[r.n] = 0;
    return r;
}
/* the first (or last) place `sub` occurs in `s`, counting from 1; 0 for none. An empty `sub` is found at 1 (at n+1 from the back). */
static int64_t rt_str_index(rt_str s, rt_str sub, bool back) {
    if (sub.n > s.n) return 0;
    if (!back) {
        for (int64_t i = 0; i + sub.n <= s.n; i++) if (sub.n == 0 || memcmp(s.p + i, sub.p, (size_t)sub.n) == 0) return i + 1;
    } else {
        for (int64_t i = s.n - sub.n; i >= 0; i--) if (sub.n == 0 || memcmp(s.p + i, sub.p, (size_t)sub.n) == 0) return i + 1;
    }
    return 0;
}
/* scan: the first (or last) place of `s` holding a character of `set`; verify: one that is not in `set`. 0 for none. */
static int64_t rt_str_scan(rt_str s, rt_str set, bool back, bool verify) {
    int64_t found = 0;
    for (int64_t i = 0; i < s.n; i++) {
        bool in = set.n > 0 && memchr(set.p, s.p[i], (size_t)set.n) != NULL;
        if (in != verify) { if (!back) return i + 1; found = i + 1; }
    }
    return found;
}
/* adjustl / adjustr: the blanks at one end moved to the other (the length stays) */
static rt_str rt_str_adjust(rt_str s, bool right, int line) {
    rt_str r = rt_str_new(rt_cs(s), s.n, line);
    int64_t lead = 0, trail = 0;
    while (lead < s.n && s.p[lead] == ' ') lead++;
    while (trail < s.n - lead && s.p[s.n - 1 - trail] == ' ') trail++;
    if (s.n == 0) return r;
    if (right) { memmove(r.p + trail, s.p, (size_t)(s.n - trail)); memset(r.p, ' ', (size_t)trail); }
    else { memmove(r.p, s.p + lead, (size_t)(s.n - lead)); memset(r.p + s.n - lead, ' ', (size_t)lead); }
    return r;
}
static rt_str rt_str_replace(rt_str s, rt_str old, rt_str nw, int line) {
    if (old.n == 0) rt_fail(line, "replace(): the text to replace must not be empty");
    int64_t count = 0;
    for (int64_t i = 0; i + old.n <= s.n; ) { if (memcmp(s.p + i, old.p, (size_t)old.n) == 0) { count++; i += old.n; } else i++; }
    int64_t n = s.n + count * (nw.n - old.n);
    rt_str r;
    r.p = (char *)malloc((size_t)n + 1);
    if (!r.p) rt_fail(line, "out of memory");
    int64_t o = 0;
    for (int64_t i = 0; i < s.n; ) {
        if (i + old.n <= s.n && memcmp(s.p + i, old.p, (size_t)old.n) == 0) { if (nw.n) memcpy(r.p + o, nw.p, (size_t)nw.n); o += nw.n; i += old.n; }
        else r.p[o++] = s.p[i++];
    }
    r.p[n] = 0; r.n = n;
    return r;
}
static int64_t rt_iachar(rt_str s, int line) {
    if (s.n != 1) rt_fail(line, "iachar(): needs a one-character string, got \"%.*s\"", (int)s.n, rt_cs(s));
    return (unsigned char)s.p[0];
}
static bool rt_str_starts(rt_str s, rt_str sub) { return sub.n <= s.n && (sub.n == 0 || memcmp(s.p, sub.p, (size_t)sub.n) == 0); }
static bool rt_str_ends(rt_str s, rt_str sub) { return sub.n <= s.n && (sub.n == 0 || memcmp(s.p + s.n - sub.n, sub.p, (size_t)sub.n) == 0); }
/* s[i]: one character */
static rt_str rt_str_char(rt_str s, int64_t i, int line) {
    if (i < 1 || i > s.n) rt_fail(line, "index %lld out of bounds 1..%lld", (long long)i, (long long)s.n);
    return rt_str_new(rt_cs(s) + i - 1, 1, line);
}
/* s[lo:hi], both ends given (the caller supplies 1 and the length for a missing one) */
static rt_str rt_str_part(rt_str s, int64_t lo, int64_t hi, const char *name, int line) {
    if (lo < 1 || hi > s.n || lo > hi + 1)
        rt_fail(line, "substring %s[%lld:%lld] is outside the string (length %lld)", name, (long long)lo, (long long)hi, (long long)s.n);
    return rt_str_new(rt_cs(s) + lo - 1, hi - lo + 1, line);
}

/* ---- files: a file value is a pointer to a handle that everything holding that file shares (NULL: never opened) ------- */
typedef struct { char *name; int mode; FILE *fh; } rt_file_s;           /* mode: 1 read, 2 write, 3 append */
typedef rt_file_s *rt_file;
static const char *rt_mode_names[] = {"", "read", "write", "append"};
static rt_file rt_open(rt_str name, rt_str mode, bool must, int line, bool *ok) {
    int m = !strcmp(rt_cs(mode), "read") ? 1 : !strcmp(rt_cs(mode), "write") ? 2 : !strcmp(rt_cs(mode), "append") ? 3 : 0;
    if (!m) rt_fail(line, "open mode must be \"read\", \"write\" or \"append\", got \"%s\"", rt_cs(mode));
    FILE *fh = fopen(rt_cs(name), m == 1 ? "r" : m == 2 ? "w" : "a");
    if (fh) {
        struct stat st;
        if (fstat(fileno(fh), &st) == 0 && S_ISDIR(st.st_mode)) { fclose(fh); fh = NULL; errno = EISDIR; }
    }
    if (!fh) {
        if (must) rt_fail(line, "cannot open '%s' for %s: %s", rt_cs(name), rt_mode_names[m], strerror(errno));
        *ok = false;
        return NULL;
    }
    if (m != 1) setvbuf(fh, NULL, _IOFBF, 8192);
    rt_file f = (rt_file)malloc(sizeof *f);
    if (!f) rt_fail(line, "out of memory");
    f->name = strdup(rt_cs(name)); f->mode = m; f->fh = fh;
    *ok = true;
    return f;
}
/* the open file an expression names; want is 0 for any, 1 for reading, 2 for writing */
static rt_file_s *rt_file_use(rt_file f, int want, int line) {
    if (!f || !f->name) rt_fail(line, "the file was never opened");
    if (!f->fh) rt_fail(line, "the file is closed ('%s')", f->name);
    if (want == 2 && f->mode == 1) rt_fail(line, "'%s' was opened for read, so it cannot be used for writeing", f->name);
    return f;
}
static void rt_file_close(rt_file f, int line) {
    rt_file_use(f, 0, line);
    fclose(f->fh); f->fh = NULL;
}
static FILE *rt_file_for_write(rt_file f, int line) { return rt_file_use(f, 2, line)->fh; }
/* the next line, or false at the end. A file is read as Python reads text: a line ends at \n, \r\n or a lone \r. The input
   (stdin) ends a line at \n only, and every \r and \n left at the end of the line is dropped. */
static bool rt_read_line(FILE *fh, rt_str *out, bool universal, int line) {
    int64_t n = 0, cap = 64;
    char *b = (char *)malloc((size_t)cap);
    if (!b) rt_fail(line, "out of memory");
    int c = getc(fh);
    if (c == EOF) { free(b); return false; }
    for (; c != EOF && c != '\n'; c = getc(fh)) {
        if (universal && c == '\r') { int d = getc(fh); if (d != '\n' && d != EOF) ungetc(d, fh); break; }
        if (c == 0 || c >= 128) rt_fail(line, "the compiled program reads ASCII text only (this line has another character)");
        if (n + 1 >= cap) { cap *= 2; b = (char *)realloc(b, (size_t)cap); if (!b) rt_fail(line, "out of memory"); }
        b[n++] = (char)c;
    }
    if (!universal) while (n > 0 && b[n - 1] == '\r') n--;
    b[n] = 0;
    out->p = b; out->n = n;
    return true;
}
static bool rt_read_line_file(rt_file f, rt_str *out, int line) {
    if (!f || !f->fh || f->mode != 1) rt_fail(line, "read_line(): needs a file that is open for reading");
    return rt_read_line(f->fh, out, true, line);
}

/* ---- gamma, log_gamma and hypot: CPython's own code (Modules/mathmodule.c: m_tgamma, m_lgamma, m_sinpi, lanczos_sum, and
   vector_norm for two arguments), the same in 3.10 to 3.13 except hypot, whose algorithm changed in 3.12 (the build picks
   the one of the Python that compiled the program: BOREAS_PY_HYPOT_OLD). The libm calls go through volatile pointers. ------- */
static int rt_gm_errno;                               /* 0, 1 (a domain error), 2 (a range error) */
#define RT_GM_PI 3.141592653589793238462643383279502884197
#define RT_GM_LOGPI 1.144729885849400174143427351353058711647
#define RT_GM_G 6.024680040776729583740234375
#define RT_GM_GH 5.524680040776729583740234375
static double (*volatile rt_fp_sin2)(double) = sin;
static double (*volatile rt_fp_cos2)(double) = cos;
static const double rt_gm_num[13] = {
    23531376880.410759688572007674451636754734846804940, 42919803642.649098768957899047001988850926355848959,
    35711959237.355668049440185451547166705960488635843, 17921034426.037209699919755754458931112671403265390,
    6039542586.3520280050642916443072979210699388420708, 1439720407.3117216736632230727949123939715485786772,
    248874557.86205415651146038641322942321632125127801, 31426415.585400194380614231628318205362874684987640,
    2876370.6289353724412254090516208496135991145378768, 186056.26539522349504029498971604569928220784236328,
    8071.6720023658162106380029022722506138218516325024, 210.82427775157934587250973392071336271166969580291,
    2.5066282746310002701649081771338373386264310793408};
static const double rt_gm_den[13] = {0.0, 39916800.0, 120543840.0, 150917976.0, 105258076.0, 45995730.0,
    13339535.0, 2637558.0, 357423.0, 32670.0, 1925.0, 66.0, 1.0};
static const double rt_gm_integral[23] = {
    1.0, 1.0, 2.0, 6.0, 24.0, 120.0, 720.0, 5040.0, 40320.0, 362880.0, 3628800.0, 39916800.0, 479001600.0, 6227020800.0,
    87178291200.0, 1307674368000.0, 20922789888000.0, 355687428096000.0, 6402373705728000.0, 121645100408832000.0,
    2432902008176640000.0, 51090942171709440000.0, 1124000727777607680000.0};
static double rt_gm_sinpi(double x) {
    double y, r;
    int n;
    y = fmod(fabs(x), 2.0);
    n = (int)round(2.0 * y);
    switch (n) {
    case 0: r = rt_fp_sin2(RT_GM_PI * y); break;
    case 1: r = rt_fp_cos2(RT_GM_PI * (y - 0.5)); break;
    case 2: r = rt_fp_sin2(RT_GM_PI * (1.0 - y)); break;
    case 3: r = -rt_fp_cos2(RT_GM_PI * (y - 1.5)); break;
    default: r = rt_fp_sin2(RT_GM_PI * (y - 2.0)); break;
    }
    return copysign(1.0, x) * r;
}
static double rt_gm_lanczos_sum(double x) {
    double num = 0.0, den = 0.0;
    int i;
    if (x < 5.0) {
        for (i = 13; --i >= 0; ) { num = num * x + rt_gm_num[i]; den = den * x + rt_gm_den[i]; }
    } else {
        for (i = 0; i < 13; i++) { num = num / x + rt_gm_num[i]; den = den / x + rt_gm_den[i]; }
    }
    return num / den;
}
static double rt_py_tgamma(double x) {
    double absx, r, y, z, sqrtpow;
    if (!isfinite(x)) {
        if (isnan(x) || x > 0.0) return x;
        rt_gm_errno = 1;
        return NAN;
    }
    if (x == 0.0) { rt_gm_errno = 1; return copysign(INFINITY, x); }
    if (x == floor(x)) {
        if (x < 0.0) { rt_gm_errno = 1; return NAN; }
        if (x <= 23) return rt_gm_integral[(int)x - 1];
    }
    absx = fabs(x);
    if (absx < 1e-20) {
        r = 1.0 / x;
        if (isinf(r)) rt_gm_errno = 2;
        return r;
    }
    if (absx > 200.0) {
        if (x < 0.0) return 0.0 / rt_gm_sinpi(x);
        rt_gm_errno = 2;
        return HUGE_VAL;
    }
    y = absx + RT_GM_GH;
    if (absx > RT_GM_GH) { double q = y - absx; z = q - RT_GM_GH; }
    else { double q = y - RT_GM_GH; z = q - absx; }
    z = z * RT_GM_G / y;
    if (x < 0.0) {
        r = -RT_GM_PI / rt_gm_sinpi(absx) / absx * rt_fp_exp(y) / rt_gm_lanczos_sum(absx);
        r -= z * r;
        if (absx < 140.0) r /= rt_fp_pow(y, absx - 0.5);
        else { sqrtpow = rt_fp_pow(y, absx / 2.0 - 0.25); r /= sqrtpow; r /= sqrtpow; }
    } else {
        r = rt_gm_lanczos_sum(absx) / rt_fp_exp(y);
        r += z * r;
        if (absx < 140.0) r *= rt_fp_pow(y, absx - 0.5);
        else { sqrtpow = rt_fp_pow(y, absx / 2.0 - 0.25); r *= sqrtpow; r *= sqrtpow; }
    }
    if (isinf(r)) rt_gm_errno = 2;
    return r;
}
static double rt_py_lgamma(double x) {
    double r, absx;
    if (!isfinite(x)) {
        if (isnan(x)) return x;
        return HUGE_VAL;
    }
    if (x == floor(x) && x <= 2.0) {
        if (x <= 0.0) { rt_gm_errno = 1; return HUGE_VAL; }
        return 0.0;
    }
    absx = fabs(x);
    if (absx < 1e-20) return -rt_fp_log(absx);
    r = rt_fp_log(rt_gm_lanczos_sum(absx)) - RT_GM_G;
    r += (absx - 0.5) * (rt_fp_log(absx + RT_GM_G - 0.5) - 1);
    if (x < 0.0) r = RT_GM_LOGPI - rt_fp_log(fabs(rt_gm_sinpi(absx))) - rt_fp_log(absx) - r;
    if (isinf(r)) rt_gm_errno = 2;
    return r;
}
/* gamma(x) or log_gamma(x) as Python's math.gamma / math.lgamma give it, with the interpreter's errors */
static double rt_gamma_call(bool lg, double x, int was_int, int line) {
    rt_gm_errno = 0;
    double r = lg ? rt_py_lgamma(x) : rt_py_tgamma(x);
    const char *name = lg ? "log_gamma" : "gamma";
#ifdef BOREAS_IEEE
    (void)was_int; (void)line; (void)name;
    if (rt_gm_errno == 1) return NAN;
    return r;
#else
    char b[48];
    if (rt_gm_errno == 1) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown(x, was_int, b));
    if (rt_gm_errno == 2 && fabs(r) >= 1.5) rt_fail(line, "%s(%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", name, rt_shown(x, was_int, b));
    return r;
#endif
}
/* single precision: the C library's tgammaf / lgammaf; a NaN or infinity from finite arguments is a domain error */
static inline float rt_gammaf(bool lg, float r, float x, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if ((r != r || isinf(r)) && isfinite(x)) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", lg ? "log_gamma" : "gamma", rt_shown((double)x, 0, b));
#else
    (void)lg; (void)x; (void)line;
#endif
    return r;
}
static inline float rt_hypotf(float a, float b, int line) {
    float r = hypotf(a, b);
#ifndef BOREAS_IEEE
    char x[48], y[48];
    if (isinf(r) && isfinite(a) && isfinite(b)) rt_fail(line, "hypot(%s, %s) is too large for a real (a file that says `use ieee` gets Infinity instead)", rt_shown((double)a, 0, x), rt_shown((double)b, 0, y));
#else
    (void)line;
#endif
    return r;
}
/* math.hypot(x, y) */
typedef struct { double hi, lo; } rt_dl;
static rt_dl rt_dl_fast_sum(double a, double b) { double x = a + b; double y = (a - x) + b; rt_dl r = {x, y}; return r; }
static rt_dl rt_dl_split(double x) { double t = x * 134217729.0; double hi = t - (t - x); rt_dl r = {hi, x - hi}; return r; }
static rt_dl rt_dl_mul(double x, double y) {
    rt_dl xx = rt_dl_split(x), yy = rt_dl_split(y);
    double p = xx.hi * yy.hi, q = xx.hi * yy.lo + xx.lo * yy.hi, z = p + q;
    rt_dl r = {z, p - z + q + xx.lo * yy.lo};
    return r;
}
static rt_dl rt_dl_mul_fma(double x, double y) { double z = x * y; rt_dl r = {z, fma(x, y, -z)}; return r; }
static double rt_vector_norm(int n, double *vec, double max, int found_nan, bool old) {
    double x, h, scale, csum = 1.0, frac1 = 0.0, frac2 = 0.0, frac3 = 0.0, oldcsum;
    int max_e, i;
    if (isinf(max)) return max;
    if (found_nan) return NAN;
    if (max == 0.0 || n <= 1) return max;
    frexp(max, &max_e);
    if (old) {                                        /* 3.10 and 3.11 */
        const double T27 = 134217729.0;
        double t, hi, lo;
        if (max_e >= -1023) {
            scale = ldexp(1.0, -max_e);
            for (i = 0; i < n; i++) {
                x = vec[i] * scale;
                t = x * T27; hi = t - (t - x); lo = x - hi;
                x = hi * hi; oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
                x = 2.0 * hi * lo; oldcsum = csum; csum += x; frac2 += (oldcsum - csum) + x;
                frac3 += lo * lo;
            }
            h = sqrt(csum - 1.0 + (frac1 + frac2 + frac3));
            x = h; t = x * T27; hi = t - (t - x); lo = x - hi;
            x = -hi * hi; oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
            x = -2.0 * hi * lo; oldcsum = csum; csum += x; frac2 += (oldcsum - csum) + x;
            x = -lo * lo; oldcsum = csum; csum += x; frac3 += (oldcsum - csum) + x;
            x = csum - 1.0 + (frac1 + frac2 + frac3);
            return (h + x / (2.0 * h)) / scale;
        }
        for (i = 0; i < n; i++) {
            x = vec[i] / max; x = x * x; oldcsum = csum; csum += x; frac1 += (oldcsum - csum) + x;
        }
        return max * sqrt(csum - 1.0 + frac1);
    }
    if (max_e < -1023) {                              /* 3.12 and later */
        double sub[2];
        for (i = 0; i < n; i++) sub[i] = vec[i] / DBL_MIN;
        return DBL_MIN * rt_vector_norm(n, sub, max / DBL_MIN, found_nan, false);
    }
    {
        rt_dl pr, sm;
        scale = ldexp(1.0, -max_e);
        for (i = 0; i < n; i++) {
            x = vec[i] * scale;
            pr = rt_dl_mul_fma(x, x);
            sm = rt_dl_fast_sum(csum, pr.hi);
            csum = sm.hi; frac1 += pr.lo; frac2 += sm.lo;
        }
        h = sqrt(csum - 1.0 + (frac1 + frac2));
        pr = rt_dl_mul_fma(-h, h);
        sm = rt_dl_fast_sum(csum, pr.hi);
        csum = sm.hi; frac1 += pr.lo; frac2 += sm.lo;
        x = csum - 1.0 + (frac1 + frac2);
        h += x / (2.0 * h);
        return h / scale;
    }
}
static double rt_hypot(double a, double b) {
    double v[2] = {fabs(a), fabs(b)};
    double max = 0.0;
    if (v[0] > max) max = v[0];
    if (v[1] > max) max = v[1];
    int nan = isnan(v[0]) || isnan(v[1]);
#ifdef BOREAS_PY_HYPOT_OLD
    return rt_vector_norm(2, v, max, nan, true);
#else
    return rt_vector_norm(2, v, max, nan, false);
#endif
}

/* ---- arrays of text (one dimension): every element is a text that the array owns --------------------------------- */
typedef struct { rt_str *data; int64_t shape[3]; int rank; int64_t n; } rt_string_arr;
static inline void rt_free_string(rt_string_arr *a) {
    if (a->data) for (int64_t i = 0; i < a->n; i++) free(a->data[i].p);
    free(a->data); a->data = NULL; a->n = 0;
}
static void rt_alloc_string(rt_string_arr *a, int rank, int64_t d0, int64_t d1, int64_t d2, int line) {
    if (d0 < 0 || d1 < 0 || d2 < 0) rt_fail(line, "array dimensions must be non-negative integers");
    rt_free_string(a);
    a->rank = rank; a->shape[0] = d0; a->shape[1] = d1; a->shape[2] = d2;
    a->n = d0 * d1 * d2;
    a->data = (rt_str *)calloc(a->n ? (size_t)a->n : 1, sizeof(rt_str));      /* a zeroed text is the empty text */
    if (!a->data) rt_fail(line, "out of memory");
}
static inline void rt_like_string(rt_string_arr *a, const int64_t *shape, int rank, int line) {
    rt_alloc_string(a, rank, shape[0], rank > 1 ? shape[1] : 1, rank > 2 ? shape[2] : 1, line);
}
static void rt_write_string_arr(const rt_string_arr *a) {
    fputc('[', rt_out);
    for (int64_t i = 0; i < a->n; i++) { if (i) fputs(", ", rt_out); fputs(rt_cs(a->data[i]), rt_out); }
    fputc(']', rt_out);
}
/* split(s, sep): the pieces between each exact sep; an empty piece stays */
static void rt_split(rt_string_arr *out, rt_str s, rt_str sep, int line) {
    if (sep.n == 0) rt_fail(line, "split(): needs a separator string that is not empty");
    int64_t count = 1;
    for (int64_t i = 0; i + sep.n <= s.n; ) {
        if (memcmp(s.p + i, sep.p, (size_t)sep.n) == 0) { count++; i += sep.n; } else i++;
    }
    rt_alloc_string(out, 1, count, 1, 1, line);
    int64_t from = 0, k = 0;
    for (int64_t i = 0; i + sep.n <= s.n; ) {
        if (memcmp(s.p + i, sep.p, (size_t)sep.n) == 0) {
            out->data[k++] = rt_str_new(s.p + from, i - from, line);
            i += sep.n; from = i;
        } else i++;
    }
    out->data[k] = rt_str_new(rt_cs(s) + from, s.n - from, line);
}
/* fields(s, extra): the pieces between runs of blanks (what Python's \s means) and of the characters in extra; never an empty piece */
static bool rt_is_field_sep(char c, rt_str extra) {
    if (c == ' ' || (c >= 9 && c <= 13) || (c >= 28 && c <= 31)) return true;
    for (int64_t i = 0; i < extra.n; i++) if (extra.p[i] == c) return true;
    return false;
}
static void rt_fields(rt_string_arr *out, rt_str s, rt_str extra, int line) {
    int64_t count = 0;
    for (int64_t i = 0; i < s.n; ) {
        if (rt_is_field_sep(s.p[i], extra)) { i++; continue; }
        count++;
        while (i < s.n && !rt_is_field_sep(s.p[i], extra)) i++;
    }
    rt_alloc_string(out, 1, count, 1, 1, line);
    int64_t k = 0;
    for (int64_t i = 0; i < s.n; ) {
        if (rt_is_field_sep(s.p[i], extra)) { i++; continue; }
        int64_t from = i;
        while (i < s.n && !rt_is_field_sep(s.p[i], extra)) i++;
        out->data[k++] = rt_str_new(s.p + from, i - from, line);
    }
}

/* ---- field, join, achar and random ----------------------------------------------------------------------------------------- */
/* field(s, k, extra): the k-th piece fields() would give; false when there is none */
static bool rt_field(rt_str *out, rt_str s, int64_t k, rt_str extra, int line) {
    if (k < 1) rt_fail(line, "field(): needs a piece number that is a positive integer");
    int64_t count = 0;
    for (int64_t i = 0; i < s.n; ) {
        if (rt_is_field_sep(s.p[i], extra)) { i++; continue; }
        int64_t from = i;
        while (i < s.n && !rt_is_field_sep(s.p[i], extra)) i++;
        if (++count == k) { *out = rt_str_new(s.p + from, i - from, line); return true; }
    }
    return false;
}
static rt_str rt_join(rt_string_arr a, rt_str sep, int line) {
    int64_t total = 0;
    for (int64_t i = 0; i < a.n; i++) total += a.data[i].n;
    if (a.n > 1) total += (a.n - 1) * sep.n;
    rt_str r;
    r.p = (char *)malloc((size_t)total + 1);
    if (!r.p) rt_fail(line, "out of memory");
    int64_t at = 0;
    for (int64_t i = 0; i < a.n; i++) {
        if (i && sep.n) { memcpy(r.p + at, sep.p, (size_t)sep.n); at += sep.n; }
        if (a.data[i].n) { memcpy(r.p + at, a.data[i].p, (size_t)a.data[i].n); at += a.data[i].n; }
    }
    r.p[at] = 0; r.n = at;
    return r;
}
static rt_str rt_achar(int64_t i, int line) {
    if (i < 0 || i > 0x10FFFF) rt_fail(line, "achar(): needs a character code from 0 to 1114111, got %lld", (long long)i);
    if (i == 0 || i >= 128) rt_fail(line, "the compiled program handles ASCII text only (achar(%lld) is another character)", (long long)i);
    char c = (char)i;
    return rt_str_new(&c, 1, line);
}
static double rt_random(void) {                       /* any generator will do: the interpreter's is not seeded either */
    static uint64_t state = 0;
    if (!state) { state = (uint64_t)time(NULL) * UINT64_C(0x9E3779B97F4A7C15) ^ (uint64_t)getpid() * UINT64_C(0xBF58476D1CE4E5B9); if (!state) state = 1; }
    state ^= state >> 12; state ^= state << 25; state ^= state >> 27;
    return (double)((state * UINT64_C(2685821657736338717)) >> 11) * (1.0 / 9007199254740992.0);
}

/* ---- formatting text: text, tf, right, left, fixed, scientific, general (ported from the interpreter's own) ---- */
static rt_str rt_str_cstr(const char *s, int line) { return rt_str_new(s, (int64_t)strlen(s), line); }
static rt_str rt_text_int(int64_t v, int line) { char b[32]; snprintf(b, sizeof b, "%lld", (long long)v); return rt_str_cstr(b, line); }
static rt_str rt_text_real(double v, int line) { char b[48]; return rt_str_cstr(rt_real_text(v, b), line); }
static rt_str rt_text_real32(float v, int line) { char b[48]; return rt_str_cstr(rt_real32_text(v, b), line); }
static rt_str rt_text_bool(bool v, int line) { return rt_str_cstr(v ? "true" : "false", line); }
static rt_str rt_str_copy(rt_str s, int line) { return rt_str_new(rt_cs(s), s.n, line); }
static void rt_need_count(const char *fn, const char *what, int64_t n, int line) {
    if (n < 0) rt_fail(line, "%s(): %s must be a whole number of 0 or more, got %lld", fn, what, (long long)n);
}
/* `n` characters of `s` right- or left-aligned in `width` (blank-filled unless `fill`), `s` returned as is when it is already as wide */
static rt_str rt_align(const char *s, int64_t n, int64_t width, char fill, bool right, int line) {
    if (n >= width) return rt_str_new(s, n, line);
    rt_str r = rt_str_new("", 0, line);
    free(r.p);
    r.p = (char *)malloc((size_t)width + 1);
    if (!r.p) rt_fail(line, "out of memory");
    memset(r.p, fill, (size_t)width);
    memcpy(r.p + (right ? width - n : 0), s, (size_t)n);
    r.p[width] = 0; r.n = width;
    return r;
}
static rt_str rt_stars(int64_t width, int line) {
    rt_str r;
    r.p = (char *)malloc((size_t)width + 1);
    if (!r.p) rt_fail(line, "out of memory");
    memset(r.p, '*', (size_t)width); r.p[width] = 0; r.n = width;
    return r;
}
/* right(x, width [, fill]) / left(...): `text` is how x shows, `is_text` says whether x was text (cut) or a number (stars) */
static rt_str rt_pad(const char *fn, rt_str text, bool is_text, int64_t width, rt_str fill, bool right, int line) {
    rt_need_count(fn, "the width", width, line);
    if (fill.n != 1) rt_fail(line, "%s(): the padding must be a one-character string", fn);
    if (width == 0) return rt_str_copy(text, line);
    if (text.n > width) return is_text ? rt_str_new(rt_cs(text), width, line) : rt_stars(width, line);
    if (right && fill.p[0] == '0' && !is_text && text.n > 0 && (text.p[0] == '-' || text.p[0] == '+')) {      /* the sign stays in front: -0042 */
        rt_str rest = rt_align(text.p + 1, text.n - 1, width - 1, '0', true, line);
        rt_str out;
        out.p = (char *)malloc((size_t)width + 1);
        if (!out.p) rt_fail(line, "out of memory");
        out.p[0] = text.p[0];
        memcpy(out.p + 1, rest.p, (size_t)rest.n + 1);
        out.n = rest.n + 1;
        rt_free_str(&rest);
        return out;
    }
    return rt_align(rt_cs(text), text.n, width, fill.p[0], right, line);
}
static char *rt_snprintf_alloc(int line, const char *fmt, int prec, double x) {
    int n = snprintf(NULL, 0, fmt, prec, x);
    char *p = (char *)malloc((size_t)n + 1);
    if (!p) rt_fail(line, "out of memory");
    snprintf(p, (size_t)n + 1, fmt, prec, x);
    return p;
}
/* NaN / Infinity as the interpreter writes them in a field of `width` (-1: no width); 0 for an ordinary number */
static int rt_special(double x, int64_t width, char *out) {
    if (x != x) { if (width < 0 || width == 0 || width >= 3) strcpy(out, "NaN"); else { memset(out, '*', (size_t)width); out[width] = 0; } return 1; }
    if (isinf(x)) {
        int neg = x < 0;
        int64_t full = neg ? 9 : 8, shortf = neg ? 4 : 3;
        if (width < 0 || width == 0 || width >= full) strcpy(out, neg ? "-Infinity" : "Infinity");
        else if (width >= shortf) strcpy(out, neg ? "-Inf" : "Inf");
        else { memset(out, '*', (size_t)width); out[width] = 0; }
        return 1;
    }
    return 0;
}
static rt_str rt_special_text(const char *s, int64_t width, int line) {
    return rt_align(s, (int64_t)strlen(s), width > 0 ? width : 0, ' ', true, line);
}
/* "0." -> "." for the first occurrence, as Python's text.replace("0.", ".", 1) */
static char *rt_drop_zero(const char *s, int line) {
    char *p = (char *)malloc(strlen(s) + 1);
    if (!p) rt_fail(line, "out of memory");
    const char *q = strstr(s, "0.");
    if (!q) { strcpy(p, s); return p; }
    size_t head = (size_t)(q - s);
    memcpy(p, s, head); strcpy(p + head, q + 1);
    return p;
}
static rt_str rt_take(char *p) { rt_str r; r.p = p; r.n = (int64_t)strlen(p); return r; }
static rt_str rt_fit_text(char *text, int64_t width, bool fortran, int line) {          /* consumes `text` */
    int64_t n = (int64_t)strlen(text);
    if (width <= 0 || n <= width) { rt_str r = rt_align(text, n, width > 0 ? width : 0, ' ', true, line); free(text); return r; }
    if (fortran) { char *t = rt_drop_zero(text, line); free(text); text = t; n = (int64_t)strlen(text); }
    rt_str r = n <= width ? rt_align(text, n, width, ' ', true, line) : rt_stars(width, line);
    free(text);
    return r;
}
static rt_str rt_fixed(double x, int64_t decimals, int64_t width, int line) {              /* INT64_MIN: no width */
    rt_need_count("fixed", "the number of decimals", decimals, line);
    if (width != INT64_MIN) rt_need_count("fixed", "the width", width, line);
    char sp[16];
    if (rt_special(x, width, sp)) return width > 0 ? rt_special_text(sp, width, line) : rt_str_cstr(sp, line);
    char *text = rt_snprintf_alloc(line, "%.*f", (int)decimals, x);
    if (decimals == 0) { char *t = (char *)malloc(strlen(text) + 2); strcpy(t, text); strcat(t, "."); free(text); text = t; }
    if (width <= 0) return rt_take(text);
    int64_t n = (int64_t)strlen(text);
    if (n > width) {
        const char *body = text + (text[0] == '-' ? 1 : 0);
        while (*body == '-') body++;                                       /* lstrip("-") */
        if (strncmp(body, "0.", 2) == 0) { char *t = rt_drop_zero(text, line); free(text); text = t; n = (int64_t)strlen(text); }
    }
    rt_str r = n <= width ? rt_align(text, n, width, ' ', true, line) : rt_stars(width, line);
    free(text);
    return r;
}
static const char *rt_minus(double x) { return signbit(x) ? "-" : ""; }
static void rt_exp_text(char *out, int e, bool fortran) {
    int a = e < 0 ? -e : e;
    if (a <= 99 || !fortran) sprintf(out, "E%c%02d", e < 0 ? '-' : '+', a);
    else sprintf(out, "%c%03d", e < 0 ? '-' : '+', a);
}
/* |x| to `digits` significant digits: the digit string (no point) and the exponent, |x| = d.ddd * 10**e */
static void rt_significant(double x, int64_t digits, char *d, int *e) {
    char *s = rt_snprintf_alloc(0, "%.*e", (int)(digits - 1), fabs(x));
    char *ex = strchr(s, 'e');
    int k = 0;
    for (char *c = s; c < ex; c++) if (*c != '.') d[k++] = *c;
    d[k] = 0;
    *e = atoi(ex + 1);
    free(s);
}
static char *rt_cat3(const char *a, const char *b, const char *c) {
    char *p = (char *)malloc(strlen(a) + strlen(b) + strlen(c) + 1);
    strcpy(p, a); strcat(p, b); strcat(p, c);
    return p;
}
static char *rt_scientific_text(double x, int64_t digits, bool fortran) {
    char ex[16];
    if (fortran) {
        if (x == 0) {
            rt_exp_text(ex, 0, true);
            char *zeros = (char *)malloc((size_t)digits + 1);
            memset(zeros, '0', (size_t)digits); zeros[digits] = 0;
            char *mid = rt_cat3("0.", zeros, ex);
            char *r = rt_cat3(rt_minus(x), mid, "");
            free(zeros); free(mid);
            return r;
        }
        char *d = (char *)malloc((size_t)digits + 8);
        int e;
        rt_significant(x, digits, d, &e);
        rt_exp_text(ex, e + 1, true);
        char *mid = rt_cat3("0.", d, ex);
        char *r = rt_cat3(rt_minus(x), mid, "");
        free(d); free(mid);
        return r;
    }
    char *s = rt_snprintf_alloc(0, "%.*e", (int)digits, fabs(x));
    char *p = strchr(s, 'e');
    int e = atoi(p + 1);
    *p = 0;
    rt_exp_text(ex, e, false);
    char *r = rt_cat3(rt_minus(x), s, ex);
    free(s);
    return r;
}
static rt_str rt_scientific(double x, int64_t digits, int64_t width, bool fortran, int line) {
    rt_need_count("scientific", "the number of digits", digits, line);
    if (width != INT64_MIN) rt_need_count("scientific", "the width", width, line);
    if (fortran && digits < 1) rt_fail(line, "scientific(): the \"fortran\" style needs at least 1 digit");
    char sp[16];
    if (rt_special(x, width, sp)) return width > 0 ? rt_special_text(sp, width, line) : rt_str_cstr(sp, line);
    return rt_fit_text(rt_scientific_text(x, digits, fortran), width, fortran, line);
}
static void rt_strip_zeros(char *s) {
    if (!strchr(s, '.')) return;
    size_t n = strlen(s);
    while (n > 0 && s[n - 1] == '0') s[--n] = 0;
    if (n > 0 && s[n - 1] == '.') s[--n] = 0;
}
static rt_str rt_general(double x, int64_t digits, int64_t width, bool fortran, int line) {
    rt_need_count("general", "the number of digits", digits, line);
    if (width != INT64_MIN) rt_need_count("general", "the width", width, line);
    if (digits < 1) rt_fail(line, "general(): needs at least 1 significant digit");
    char sp[16];
    if (rt_special(x, width, sp)) return width > 0 ? rt_special_text(sp, width, line) : rt_str_cstr(sp, line);
    char dg[400]; int e = 0;
    if (fortran) {
        int k = 0;
        if (x != 0) { char *d = (char *)malloc((size_t)digits + 8); rt_significant(x, digits, d, &e); free(d); k = e + 1; }
        if (x == 0 || (0 <= k && k <= digits)) {
            int64_t decimals = x == 0 ? digits - 1 : digits - k;
            int64_t w = width > 0 ? (width - 4 > 0 ? width - 4 : 0) : INT64_MIN;
            rt_str body = rt_fixed(x, decimals, w, line);
            if (strchr(rt_cs(body), '*')) { rt_free_str(&body); return rt_stars(width, line); }
            char *t = rt_cat3(rt_cs(body), "    ", "");
            rt_free_str(&body);
            rt_str r = rt_align(t, (int64_t)strlen(t), width > 0 ? width : 0, ' ', true, line);
            free(t);
            return r;
        }
        return rt_fit_text(rt_scientific_text(x, digits, true), width, true, line);
    }
    char *text;
    if (x == 0) text = rt_cat3(rt_minus(x), "0", "");
    else {
        char *d = (char *)malloc((size_t)digits + 8);
        rt_significant(x, digits, d, &e);
        free(d);
        if (e < -4 || e >= digits) {
            char *s = rt_snprintf_alloc(line, "%.*e", (int)(digits - 1), fabs(x));
            *strchr(s, 'e') = 0;
            rt_strip_zeros(s);
            char ex[16]; rt_exp_text(ex, e, false);
            text = rt_cat3(rt_minus(x), s, ex);
            free(s);
        } else {
            char *s = rt_snprintf_alloc(line, "%.*f", (int)(digits - 1 - e), fabs(x));
            rt_strip_zeros(s);
            text = rt_cat3(rt_minus(x), s, "");
            free(s);
        }
    }
    (void)dg;
    return rt_fit_text(text, width, false, line);
}

/* ---- maps and sets: a hash table that remembers the order the keys were added in ----
   A key is a whole number (kind 0) or text (kind 1); a map's value is a whole number (0), real (1), text (2) or logical (3),
   and a set has no value (-1). The entries sit in the order they were added; a removed one leaves a gap until the table is
   compacted, so the order never changes, and a key added again after being removed goes to the end (as in a Python dict). */
typedef union { int64_t i; double d; bool b; rt_str s; void *p; } rt_cell;
typedef struct { rt_cell key, val; uint64_t hash; bool live; } rt_entry;
typedef struct {
    int kk, vk;                      /* key kind, value kind */
    rt_entry *e; int64_t n, cap;     /* the entries (gaps included) and how many there is room for */
    int64_t size;                    /* the live entries */
    int64_t *idx, icap;              /* the index: entry number + 1 (0 = empty), a power of two at least twice cap */
} rt_map;
static inline rt_cell rt_ki(int64_t x) { rt_cell c; memset(&c, 0, sizeof c); c.i = x; return c; }
static inline rt_cell rt_ks(rt_str x) { rt_cell c; memset(&c, 0, sizeof c); c.s = x; return c; }
static inline rt_cell rt_vd(double x) { rt_cell c; memset(&c, 0, sizeof c); c.d = x; return c; }
static inline rt_cell rt_vp(void *x) { rt_cell c; memset(&c, 0, sizeof c); c.p = x; return c; }
static inline rt_cell rt_vb(bool x) { rt_cell c; memset(&c, 0, sizeof c); c.b = x; return c; }
static uint64_t rt_hash_key(int kk, rt_cell k) {
    uint64_t z;
    if (kk == 0) z = (uint64_t)k.i + 0x9e3779b97f4a7c15ULL;
    else { z = 1469598103934665603ULL; for (int64_t i = 0; i < k.s.n; i++) z = (z ^ (unsigned char)k.s.p[i]) * 1099511628211ULL; z += 0x9e3779b97f4a7c15ULL; }
    z = (z ^ (z >> 30)) * 0xbf58476d1ce4e5b9ULL;
    z = (z ^ (z >> 27)) * 0x94d049bb133111ebULL;
    return z ^ (z >> 31);
}
static inline bool rt_key_eq(int kk, rt_cell a, rt_cell b) {
    return kk == 0 ? a.i == b.i : (a.s.n == b.s.n && (a.s.n == 0 || memcmp(a.s.p, b.s.p, (size_t)a.s.n) == 0));
}
static inline rt_cell rt_cell_copy(bool text, rt_cell c) {
    if (text) c.s = rt_str_new(rt_cs(c.s), c.s.n, 0);
    return c;
}
/* a map's value can be a set (kind 4), or an array of whole numbers (5) or reals (6): a heap object the cell points to, owned by the map */
static void *rt_nested_copy(int vk, const void *src, int line);
static void rt_nested_free(int vk, void *p);
static inline rt_cell rt_val_copy(int vk, rt_cell c, int line) {
    if (vk == 2) return rt_cell_copy(true, c);
    if (vk >= 4) c.p = rt_nested_copy(vk, c.p, line);
    return c;
}
static inline void rt_val_free(int vk, rt_cell c) {
    if (vk == 2) free(c.s.p);
    else if (vk >= 4) rt_nested_free(vk, c.p);
}
static void rt_map_free(rt_map *m) {
    for (int64_t i = 0; i < m->n; i++) if (m->e[i].live) {
        if (m->kk == 1) free(m->e[i].key.s.p);
        rt_val_free(m->vk, m->e[i].val);
    }
    free(m->e); free(m->idx);
    m->e = NULL; m->idx = NULL; m->n = m->cap = m->size = m->icap = 0;
}
static inline void rt_free_map(rt_map *m) { rt_map_free(m); }          /* (the name the generated code frees a map by) */
static void rt_map_reset(rt_map *m, int kk, int vk) { rt_map_free(m); m->kk = kk; m->vk = vk; }
static int64_t rt_map_find(const rt_map *m, rt_cell key, uint64_t h) {
    if (m->icap == 0) return -1;
    uint64_t mask = (uint64_t)m->icap - 1, p = h & mask;
    for (;;) {
        int64_t k = m->idx[p];
        if (k == 0) return -1;
        const rt_entry *en = &m->e[k - 1];
        if (en->live && en->hash == h && rt_key_eq(m->kk, en->key, key)) return k - 1;
        p = (p + 1) & mask;
    }
}
static void rt_map_index(rt_map *m, int line) {               /* (re)build the index of the entries there are */
    free(m->idx);
    m->icap = m->cap * 2;
    m->idx = (int64_t *)calloc((size_t)m->icap, sizeof(int64_t));
    if (!m->idx) rt_fail(line, "out of memory");
    for (int64_t i = 0; i < m->n; i++) {
        uint64_t mask = (uint64_t)m->icap - 1, p = m->e[i].hash & mask;
        while (m->idx[p]) p = (p + 1) & mask;
        m->idx[p] = i + 1;
    }
}
static void rt_map_room(rt_map *m, int line) {                /* make room for one more entry: close the gaps, and grow if more than half is used */
    int64_t o = 0;
    for (int64_t i = 0; i < m->n; i++) if (m->e[i].live) { if (o != i) m->e[o] = m->e[i]; o++; }
    m->n = o;
    if (m->cap == 0 || m->size * 2 >= m->cap) {
        int64_t cap = m->cap ? m->cap * 2 : 8;
        rt_entry *e = (rt_entry *)realloc(m->e, (size_t)cap * sizeof(rt_entry));
        if (!e) rt_fail(line, "out of memory");
        m->e = e; m->cap = cap;
    }
    rt_map_index(m, line);
}
/* store `val` under `key` (both copied): a new key goes at the end, an old one keeps its place */
static void rt_map_put(rt_map *m, rt_cell key, rt_cell val, int line) {
    uint64_t h = rt_hash_key(m->kk, key);
    int64_t k = rt_map_find(m, key, h);
    if (k >= 0) {
        if (m->vk == 2 || m->vk >= 4) { rt_cell c = rt_val_copy(m->vk, val, line); rt_val_free(m->vk, m->e[k].val); m->e[k].val = c; }
        else if (m->vk >= 0) m->e[k].val = val;
        return;
    }
    if (m->n == m->cap) rt_map_room(m, line);
    rt_entry *en = &m->e[m->n];
    en->key = rt_cell_copy(m->kk == 1, key);
    if (m->vk >= 0) en->val = rt_val_copy(m->vk, val, line); else memset(&en->val, 0, sizeof en->val);
    en->hash = h; en->live = true;
    uint64_t mask = (uint64_t)m->icap - 1, p = h & mask;
    while (m->idx[p]) p = (p + 1) & mask;
    m->idx[p] = m->n + 1;
    m->n++; m->size++;
}
static rt_entry *rt_map_get(const rt_map *m, rt_cell key) {   /* the entry of `key`, or NULL */
    int64_t k = rt_map_find(m, key, rt_hash_key(m->kk, key));
    return k < 0 ? NULL : &m->e[k];
}
static char *rt_key_show(int kk, rt_cell key) {                /* a key as a message shows it: 5 or "ann" (never freed: the program stops) */
    char *b = (char *)malloc(kk == 1 ? (size_t)key.s.n + 48 : 48);
    if (kk == 0) snprintf(b, 48, "%lld", (long long)key.i);
    else snprintf(b, (size_t)key.s.n + 48, "\"%.*s\"", (int)key.s.n, rt_cs(key.s));
    return b;
}
static rt_entry *rt_map_at(const rt_map *m, rt_cell key, const char *name, int line) {
    rt_entry *en = rt_map_get(m, key);
    if (!en) rt_fail(line, "the map '%s' has no key %s (test with has(%s, key), or use get(%s, key) ?? default)", name, rt_key_show(m->kk, key), name, name);
    return en;
}
static void rt_map_remove(rt_map *m, rt_cell key, const char *name, int line) {
    int64_t k = rt_map_find(m, key, rt_hash_key(m->kk, key));
    if (k < 0) rt_fail(line, "cannot remove %s: '%s' has no such %s (test with has first)", rt_key_show(m->kk, key), name, m->vk >= 0 ? "key" : "member");
    if (m->kk == 1) free(m->e[k].key.s.p);
    rt_val_free(m->vk, m->e[k].val);
    m->e[k].live = false;
    m->size--;
}
static void rt_map_copy(rt_map *dst, const rt_map *src, int line) {   /* dst is empty (zeroed) */
    dst->kk = src->kk; dst->vk = src->vk;
    for (int64_t i = 0; i < src->n; i++) if (src->e[i].live) rt_map_put(dst, src->e[i].key, src->e[i].val, line);
}
static void rt_map_set(rt_map *dst, const rt_map *src, int line) {    /* dst = src: copy first, so `a = a` is safe */
    rt_map c; memset(&c, 0, sizeof c);
    rt_map_copy(&c, src, line);
    rt_map_free(dst);
    *dst = c;
}
static bool rt_map_equal(const rt_map *a, const rt_map *b);
static bool rt_map_equal(const rt_map *a, const rt_map *b) {          /* the same keys with equal values, in any order */
    if (a->size != b->size) return false;
    for (int64_t i = 0; i < a->n; i++) if (a->e[i].live) {
        rt_entry *o = rt_map_get(b, a->e[i].key);
        if (!o) return false;
        if (a->vk == 0 && a->e[i].val.i != o->val.i) return false;
        if (a->vk == 1 && !(a->e[i].val.d == o->val.d)) return false;
        if (a->vk == 3 && a->e[i].val.b != o->val.b) return false;
        if (a->vk == 2 && !rt_key_eq(1, a->e[i].val, o->val)) return false;
        if (a->vk == 4 && !rt_map_equal((const rt_map *)a->e[i].val.p, (const rt_map *)o->val.p)) return false;
    }
    return true;
}
/* the entries as they are when a `do key, value in m` loop starts (the loop walks this, so the body may change the map) */
typedef struct { rt_cell *k, *v; int64_t n; int kk, vk; } rt_snap;
static void *rt_nested_copy(int vk, const void *src, int line) {
    if (vk == 4) { rt_map *d = (rt_map *)calloc(1, sizeof(rt_map)); if (!d) rt_fail(line, "out of memory"); rt_map_copy(d, (const rt_map *)src, line); return d; }
    if (vk == 5) {
        const rt_int_arr *a = (const rt_int_arr *)src; rt_int_arr *d = (rt_int_arr *)calloc(1, sizeof(rt_int_arr));
        if (!d) rt_fail(line, "out of memory");
        rt_alloc_int(d, a->rank, a->shape[0], a->shape[1], a->shape[2], line);
        if (a->n) memcpy(d->data, a->data, (size_t)a->n * sizeof(int64_t));
        return d;
    }
    const rt_real_arr *a = (const rt_real_arr *)src; rt_real_arr *d = (rt_real_arr *)calloc(1, sizeof(rt_real_arr));
    if (!d) rt_fail(line, "out of memory");
    rt_alloc_real(d, a->rank, a->shape[0], a->shape[1], a->shape[2], line);
    if (a->n) memcpy(d->data, a->data, (size_t)a->n * sizeof(double));
    return d;
}
static void rt_nested_free(int vk, void *p) {
    if (vk == 4) rt_map_free((rt_map *)p);
    else if (vk == 5) rt_free_int((rt_int_arr *)p);
    else rt_free_real((rt_real_arr *)p);
    free(p);
}
static void rt_free_snap(rt_snap *s) {
    for (int64_t i = 0; i < s->n; i++) {
        if (s->kk == 1) free(s->k[i].s.p);
        if (s->vk == 2) free(s->v[i].s.p);
    }
    free(s->k); free(s->v);
    s->k = s->v = NULL; s->n = 0;
}
static void rt_snap_take(rt_snap *s, const rt_map *m, int line) {
    rt_free_snap(s);
    s->kk = m->kk; s->vk = m->vk; s->n = m->size;
    s->k = (rt_cell *)malloc(((size_t)m->size + 1) * sizeof(rt_cell));
    s->v = (rt_cell *)malloc(((size_t)m->size + 1) * sizeof(rt_cell));
    if (!s->k || !s->v) rt_fail(line, "out of memory");
    int64_t o = 0;
    for (int64_t i = 0; i < m->n; i++) if (m->e[i].live) {
        s->k[o] = rt_cell_copy(m->kk == 1, m->e[i].key);
        if (m->vk >= 0 && m->vk < 4) s->v[o] = rt_cell_copy(m->vk == 2, m->e[i].val); else memset(&s->v[o], 0, sizeof s->v[o]);
        o++;
    }
}
static void rt_write_collection(rt_map m);
static void rt_write_cell(int kind, rt_cell c, bool key) {
    if (!key && kind == 4) { rt_write_collection(*(rt_map *)c.p); return; }
    if (!key && kind == 5) { rt_write_int_arr((const rt_int_arr *)c.p); return; }
    if (!key && kind == 6) { rt_write_real_arr((const rt_real_arr *)c.p); return; }
    if (key) { if (kind == 0) rt_write_int(c.i); else rt_write_string(c.s); return; }
    if (kind == 0) rt_write_int(c.i); else if (kind == 1) rt_write_real(c.d); else if (kind == 2) rt_write_string(c.s); else rt_write_bool(c.b);
}
static void rt_write_collection(rt_map m) {                            /* {ann: 31, bob: 27} and {x, y} */
    bool first = true;
    fputc('{', rt_out);
    for (int64_t i = 0; i < m.n; i++) if (m.e[i].live) {
        if (!first) fputs(", ", rt_out);
        first = false;
        rt_write_cell(m.kk, m.e[i].key, true);
        if (m.vk >= 0) { fputs(": ", rt_out); rt_write_cell(m.vk, m.e[i].val, false); }
    }
    fputc('}', rt_out);
}

/* the command line and the file system */
static int rt_argc = 1;
static char **rt_argv = NULL;
static rt_str rt_argument(int64_t i, int line) {            /* argument(i) for i >= 1: the text, or "" past the last */
    if (i < rt_argc && rt_argv) return rt_str_new(rt_argv[i], (int64_t)strlen(rt_argv[i]), line);
    return rt_str_new("", 0, line);
}
static bool rt_exists(rt_str name) { struct stat st; return stat(rt_cs(name), &st) == 0; }
