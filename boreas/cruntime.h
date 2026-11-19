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

static void rt_write_int(int64_t v) { printf("%lld", (long long)v); }
static void rt_write_real(double v) { char b[48]; fputs(rt_real_text(v, b), stdout); }
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
static void rt_write_real32(float v) { char b[48]; fputs(rt_real32_text(v, b), stdout); }
static void rt_write_bool(bool v) { fputs(v ? "true" : "false", stdout); }
static void rt_write_str(const char *s) { fputs(s, stdout); }
static void rt_write_sep(void) { fputc(' ', stdout); }
static void rt_write_nl(void) { fputc('\n', stdout); }
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
    (void)name; (void)x; (void)was_int; (void)line;
#endif
    return r;
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
    if (b == 0.0) { char x[48], y[48]; rt_fail(line, "mod(%s, %s): division by zero", rt_shown(a, a_int, x), rt_shown(b, b_int, y)); }
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
#ifndef BOREAS_RELEASE
static int rt_depth = 0;
static inline void rt_call_in(int line, const char *name) {
    if (++rt_depth > 10000) rt_fail(line, "'%s' is nested more than 10000 calls deep: a recursive call that never stops?", name);
}
static inline void rt_call_out(void) { rt_depth--; }
#else
static inline void rt_call_in(int line, const char *name) { (void)line; (void)name; }
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
RT_DEFINE_ARRAY(int, int64_t)
RT_DEFINE_ARRAY(int32, int32_t)
RT_DEFINE_ARRAY(real, double)
RT_DEFINE_ARRAY(real32, float)
RT_DEFINE_ARRAY(logical, bool)

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
            fputc('[', stdout);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) { if (i) fputs(", ", stdout); WRITE(a->data[i]); }           \
            fputc(']', stdout);                                                                                    \
        } else if (a->rank == 2) {                                                                                 \
            fputc('[', stdout);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) {                                                            \
                if (i) fputs(", ", stdout);                                                                        \
                fputc('[', stdout);                                                                                \
                for (int64_t j = 0; j < a->shape[1]; j++) { if (j) fputs(", ", stdout); WRITE(a->data[i + j * a->shape[0]]); } \
                fputc(']', stdout);                                                                                \
            }                                                                                                      \
            fputc(']', stdout);                                                                                    \
        } else {                                                                                                   \
            fputc('[', stdout);                                                                                    \
            for (int64_t i = 0; i < a->shape[0]; i++) {                                                            \
                if (i) fputs(", ", stdout);                                                                        \
                fputc('[', stdout);                                                                                \
                for (int64_t j = 0; j < a->shape[1]; j++) {                                                        \
                    if (j) fputs(", ", stdout);                                                                    \
                    fputc('[', stdout);                                                                            \
                    for (int64_t k = 0; k < a->shape[2]; k++) {                                                    \
                        if (k) fputs(", ", stdout);                                                                \
                        WRITE(a->data[i + j * a->shape[0] + k * a->shape[0] * a->shape[1]]);                       \
                    }                                                                                              \
                    fputc(']', stdout);                                                                            \
                }                                                                                                  \
                fputc(']', stdout);                                                                                \
            }                                                                                                      \
            fputc(']', stdout);                                                                                    \
        }                                                                                                          \
    }
RT_DEFINE_WRITE(int, int64_t, rt_write_int)
RT_DEFINE_WRITE(int32, int32_t, rt_write_int)
RT_DEFINE_WRITE(real, double, rt_write_real)
RT_DEFINE_WRITE(real32, float, rt_write_real32)
RT_DEFINE_WRITE(logical, bool, rt_write_bool)

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
    if (r != r && x == x) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown((double)x, 0, b));
    if (isinf(r) && isfinite(x)) rt_fail(line, "%s(%s) is too large for a real (a file that says `use ieee` gets Infinity instead)", name, rt_shown((double)x, 0, b));
#else
    (void)name; (void)x; (void)line;
#endif
    return r;
}
static inline float rt_logf(float x, float (*fn)(float), const char *name, int line) {
#ifndef BOREAS_IEEE
    char b[48];
    if (x <= 0.0f) rt_fail(line, "%s(%s) has no real answer (a file that says `use ieee` gets NaN instead)", name, rt_shown((double)x, 0, b));
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
    if (fabsf(x) >= 1.0f) rt_fail(line, "atanh(%s) has no real answer (a file that says `use ieee` gets NaN instead)", rt_shown((double)x, 0, b));
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
    if (q - q != 0) rt_fail(line, "modulo(): cannot convert float %s to integer", q != q ? "NaN" : "infinity");
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
