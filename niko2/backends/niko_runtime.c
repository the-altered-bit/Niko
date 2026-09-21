/* Niko native runtime (Alpha 9).
 *
 * Boxed value model mirroring the WASM backend: every Niko value is a
 * heap-allocated NVal*. Memory is malloc'd and never freed — Niko programs
 * are short-lived; a GC is future work.
 */
#include "niko_runtime.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <math.h>
#include <time.h>
#include <sys/time.h>

/* ---------------- memory ---------------- */

static void *xmalloc(size_t n) {
    void *p = malloc(n ? n : 1);
    if (!p) { fprintf(stderr, "Niko error: out of memory.\n"); exit(1); }
    return p;
}

static void *xrealloc(void *p, size_t n) {
    void *q = realloc(p, n ? n : 1);
    if (!q) { fprintf(stderr, "Niko error: out of memory.\n"); exit(1); }
    return q;
}

static NVal *nval_new(int tag) {
    NVal *v = xmalloc(sizeof(NVal));
    v->tag = tag;
    return v;
}

/* ---------------- singletons ---------------- */

static NVal g_nothing = { NVAL_NOTHING, { .num = 0 } };
static NVal g_yes = { NVAL_YESNO, { .yn = 1 } };
static NVal g_no = { NVAL_YESNO, { .yn = 0 } };

NVal *nval_nothing(void) { return &g_nothing; }
NVal *nval_yes(void) { return &g_yes; }
NVal *nval_no(void) { return &g_no; }

/* ---------------- constructors ---------------- */

NVal *nval_number(double d) {
    NVal *v = nval_new(NVAL_NUMBER);
    v->u.num = d;
    return v;
}

NVal *nval_text(const char *s, int32_t len) {
    NVal *v = nval_new(NVAL_TEXT);
    v->u.text.data = xmalloc((size_t)len);
    memcpy(v->u.text.data, s, (size_t)len);
    v->u.text.len = len;
    return v;
}

NVal *nval_text_cstr(const char *s) {
    return nval_text(s, (int32_t)strlen(s));
}

NVal *nval_list(void) {
    NVal *v = nval_new(NVAL_LIST);
    v->u.list.cap = 4;
    v->u.list.len = 0;
    v->u.list.items = xmalloc(sizeof(NVal *) * 4);
    return v;
}

void nval_list_push(NVal *l, NVal *x) {
    if (l->u.list.len == l->u.list.cap) {
        l->u.list.cap *= 2;
        l->u.list.items = xrealloc(l->u.list.items,
                                  sizeof(NVal *) * (size_t)l->u.list.cap);
    }
    l->u.list.items[l->u.list.len++] = x;
}

NVal *nval_record(void) {
    NVal *v = nval_new(NVAL_RECORD);
    v->u.rec.cap = 4;
    v->u.rec.len = 0;
    v->u.rec.keys = xmalloc(sizeof(char *) * 4);
    v->u.rec.klen = xmalloc(sizeof(int32_t) * 4);
    v->u.rec.vals = xmalloc(sizeof(NVal *) * 4);
    return v;
}

static int rec_find(NVal *r, const char *k, int32_t klen) {
    for (int32_t i = 0; i < r->u.rec.len; i++)
        if (r->u.rec.klen[i] == klen &&
            memcmp(r->u.rec.keys[i], k, (size_t)klen) == 0)
            return i;
    return -1;
}

int nval_record_has(NVal *r, const char *k, int32_t klen) {
    return rec_find(r, k, klen) >= 0;
}

void nval_record_set(NVal *r, const char *k, int32_t klen, NVal *v) {
    int i = rec_find(r, k, klen);
    if (i >= 0) { r->u.rec.vals[i] = v; return; }
    if (r->u.rec.len == r->u.rec.cap) {
        r->u.rec.cap *= 2;
        r->u.rec.keys = xrealloc(r->u.rec.keys, sizeof(char *) * (size_t)r->u.rec.cap);
        r->u.rec.klen = xrealloc(r->u.rec.klen, sizeof(int32_t) * (size_t)r->u.rec.cap);
        r->u.rec.vals = xrealloc(r->u.rec.vals, sizeof(NVal *) * (size_t)r->u.rec.cap);
    }
    char *kc = xmalloc((size_t)klen);
    memcpy(kc, k, (size_t)klen);
    r->u.rec.keys[r->u.rec.len] = kc;
    r->u.rec.klen[r->u.rec.len] = klen;
    r->u.rec.vals[r->u.rec.len] = v;
    r->u.rec.len++;
}

NVal *nval_ok(NVal *v) {
    NVal *r = nval_new(NVAL_RESULT);
    r->u.res.ok = 1;
    r->u.res.val = v;
    return r;
}

NVal *nval_error(NVal *msg_text) {
    NVal *r = nval_new(NVAL_RESULT);
    r->u.res.ok = 0;
    r->u.res.val = msg_text;
    return r;
}

NVal *nval_function(int32_t id, const char *name, int32_t nlen, NVal *env) {
    NVal *v = nval_new(NVAL_FUNCTION);
    v->u.fn.func_id = id;
    v->u.fn.name = xmalloc((size_t)nlen + 1);
    memcpy(v->u.fn.name, name, (size_t)nlen);
    v->u.fn.name[nlen] = '\0';
    v->u.fn.nlen = nlen;
    v->u.fn.env = env;
    return v;
}

/* A cell is a 1-element list: shared by pointer, so every holder of the
 * cell sees the same current value (capture by reference). */
NVal *nval_cell(NVal *v) {
    NVal *c = nval_list();
    nval_list_push(c, v);
    return c;
}

NVal *nval_cell_get(NVal *c) { return c->u.list.items[0]; }
void nval_cell_set(NVal *c, NVal *v) { c->u.list.items[0] = v; }

/* ---------------- panic ---------------- */

_Noreturn void niko_panic(int line, const char *msg) {
    if (line > 0)
        fprintf(stderr, "Niko error at line %d: %s\n", line, msg);
    else
        fprintf(stderr, "Niko error: %s\n", msg);
    exit(1);
}

_Noreturn void niko_arity_panic(const char *name, int want, int got) {
    char msg[1024];
    snprintf(msg, sizeof msg, "%s expected %d arguments, got %d.",
             name, want, got);
    niko_panic(0, msg);
}

/* ---------------- UTF-8 helpers ---------------- */

/* Count Unicode code points in a UTF-8 byte string. */
static int32_t utf8_len(const char *s, int32_t n) {
    int32_t c = 0;
    for (int32_t i = 0; i < n; i++)
        if ((s[i] & 0xC0) != 0x80) c++;
    return c;
}

/* Byte offset of the k-th code point (0-based). */
static int32_t utf8_byte_offset(const char *s, int32_t n, int32_t k) {
    int32_t i = 0, c = 0;
    while (i < n && c < k) {
        unsigned char ch = (unsigned char)s[i];
        i += (ch < 0x80) ? 1 : (ch < 0xE0) ? 2 : (ch < 0xF0) ? 3 : 4;
        c++;
    }
    return i;
}

/* ---------------- growable byte buffer ---------------- */

typedef struct { char *data; int32_t len, cap; } sbuf;

static void sb_init(sbuf *b) {
    b->cap = 64; b->len = 0;
    b->data = xmalloc(64);
}

static void sb_put(sbuf *b, const char *s, int32_t n) {
    if (b->len + n > b->cap) {
        while (b->len + n > b->cap) b->cap *= 2;
        b->data = xrealloc(b->data, (size_t)b->cap);
    }
    memcpy(b->data + b->len, s, (size_t)n);
    b->len += n;
}

static void sb_ch(sbuf *b, char c) { sb_put(b, &c, 1); }

/* ---------------- truthy / equals ---------------- */

int nval_truthy(NVal *v) {
    switch (v->tag) {
    case NVAL_NOTHING: return 0;
    case NVAL_YESNO:   return v->u.yn;
    case NVAL_NUMBER:   return v->u.num != 0.0;
    case NVAL_TEXT:    return v->u.text.len != 0;
    case NVAL_LIST:    return v->u.list.len != 0;
    case NVAL_RECORD:  return v->u.rec.len != 0;
    case NVAL_RESULT:  return v->u.res.ok;
    case NVAL_FUNCTION: return 1;   /* unreachable: checker needs booleans */
    }
    return 0;
}

/* Python-like equality: number == yesno cross-compares (1 == True). */
int nval_equals(NVal *a, NVal *b) {
    int ta = a->tag, tb = b->tag;
    if (ta == NVAL_NUMBER && tb == NVAL_YESNO)
        return a->u.num == (double)b->u.yn;
    if (ta == NVAL_YESNO && tb == NVAL_NUMBER)
        return (double)a->u.yn == b->u.num;
    if (ta != tb) return 0;
    switch (ta) {
    case NVAL_NOTHING: return 1;
    case NVAL_NUMBER:   return a->u.num == b->u.num;
    case NVAL_YESNO:    return a->u.yn == b->u.yn;
    case NVAL_TEXT:
        return a->u.text.len == b->u.text.len &&
               memcmp(a->u.text.data, b->u.text.data,
                      (size_t)a->u.text.len) == 0;
    case NVAL_LIST: {
        if (a->u.list.len != b->u.list.len) return 0;
        for (int32_t i = 0; i < a->u.list.len; i++)
            if (!nval_equals(a->u.list.items[i], b->u.list.items[i]))
                return 0;
        return 1;
    }
    case NVAL_RECORD: {
        if (a->u.rec.len != b->u.rec.len) return 0;
        for (int32_t i = 0; i < a->u.rec.len; i++) {
            int j = rec_find(b, a->u.rec.keys[i], a->u.rec.klen[i]);
            if (j < 0 || !nval_equals(a->u.rec.vals[i], b->u.rec.vals[j]))
                return 0;
        }
        return 1;
    }
    case NVAL_RESULT: {
        if (a->u.res.ok != b->u.res.ok) return 0;
        if (a->u.res.ok)
            return nval_equals(a->u.res.val, b->u.res.val);
        NVal *ma = a->u.res.val, *mb = b->u.res.val;
        return ma->u.text.len == mb->u.text.len &&
               memcmp(ma->u.text.data, mb->u.text.data,
                      (size_t)ma->u.text.len) == 0;
    }
    case NVAL_FUNCTION: return a == b;   /* identity, like the VM */
    }
    return 0;
}

/* ---------------- text formatting (fmt semantics) ---------------- */

static void fmt_into(sbuf *b, NVal *v);       /* forward */

static void fmt_number(sbuf *b, double d) {
    char tmp[64];
    if (isnan(d)) { sb_put(b, "nan", 3); return; }
    if (isinf(d)) {
        sb_put(b, d > 0 ? "inf" : "-inf", d > 0 ? 3 : 4);
        return;
    }
    if (d == floor(d)) {
        if (fabs(d) < 9007199254740992.0)
            snprintf(tmp, sizeof tmp, "%lld", (long long)d);
        else
            snprintf(tmp, sizeof tmp, "%.0f", d);  /* beyond int64: full digits */
        sb_put(b, tmp, (int32_t)strlen(tmp));
        return;
    }
    /* shortest string that round-trips, like Python repr/str */
    for (int p = 1; p <= 17; p++) {
        snprintf(tmp, sizeof tmp, "%.*g", p, d);
        if (strtod(tmp, NULL) == d) break;
    }
    sb_put(b, tmp, (int32_t)strlen(tmp));
}

/* JSON-ish quoting for text nested inside lists/records. */
static void fmt_quoted(sbuf *b, const char *s, int32_t n) {
    sb_ch(b, '"');
    for (int32_t i = 0; i < n; i++) {
        char c = s[i];
        if (c == '"') sb_put(b, "\\\"", 2);
        else if (c == '\\') sb_put(b, "\\\\", 2);
        else if (c == '\n') sb_put(b, "\\n", 2);
        else if (c == '\t') sb_put(b, "\\t", 2);
        else sb_ch(b, c);
    }
    sb_ch(b, '"');
}

static void fmt_nested(sbuf *b, NVal *v) {
    if (v->tag == NVAL_TEXT)
        fmt_quoted(b, v->u.text.data, v->u.text.len);
    else
        fmt_into(b, v);
}

static void fmt_into(sbuf *b, NVal *v) {
    switch (v->tag) {
    case NVAL_NOTHING: sb_put(b, "nothing", 7); break;
    case NVAL_YESNO:   sb_put(b, v->u.yn ? "yes" : "no", v->u.yn ? 3 : 2); break;
    case NVAL_NUMBER:   fmt_number(b, v->u.num); break;
    case NVAL_TEXT:    sb_put(b, v->u.text.data, v->u.text.len); break;
    case NVAL_LIST:
        sb_ch(b, '[');
        for (int32_t i = 0; i < v->u.list.len; i++) {
            if (i) sb_put(b, ", ", 2);
            fmt_nested(b, v->u.list.items[i]);
        }
        sb_ch(b, ']');
        break;
    case NVAL_RECORD:
        sb_ch(b, '{');
        for (int32_t i = 0; i < v->u.rec.len; i++) {
            if (i) sb_put(b, ", ", 2);
            sb_put(b, v->u.rec.keys[i], v->u.rec.klen[i]);  /* keys unquoted */
            sb_put(b, ": ", 2);
            fmt_nested(b, v->u.rec.vals[i]);
        }
        sb_ch(b, '}');
        break;
    case NVAL_RESULT:
        if (v->u.res.ok) {
            sb_put(b, "ok(", 3);
            fmt_into(b, v->u.res.val);
            sb_ch(b, ')');
        } else {
            /* the VM wraps the message in raw quotes, no escaping */
            sb_put(b, "error(\"", 7);
            sb_put(b, v->u.res.val->u.text.data, v->u.res.val->u.text.len);
            sb_put(b, "\")", 2);
        }
        break;
    case NVAL_FUNCTION:
        sb_put(b, "function \"", 10);
        sb_put(b, v->u.fn.name, v->u.fn.nlen);
        sb_ch(b, '"');
        break;
    }
}

NVal *nval_to_text(NVal *v) {
    sbuf b;
    sb_init(&b);
    fmt_into(&b, v);
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return t;
}

/* ---------------- first-class calls ---------------- */

NVal *niko_call(int line, NVal *fn, int nargs, NVal **args) {
    if (fn->tag != NVAL_FUNCTION) {
        /* same text as the VM: value formatted with say-formatting */
        sbuf b;
        sb_init(&b);
        sb_put(&b, "I can't call ", 13);
        fmt_into(&b, fn);
        sb_put(&b, " as a function.", 15);
        sb_ch(&b, '\0');
        niko_panic(line, b.data);   /* leaks b.data; we exit anyway */
    }
    return niko_call_dispatch(line, fn->u.fn.func_id, fn->u.fn.env,
                              nargs, args);
}

/* ---------------- binary / unary ---------------- */

static int64_t as_i64(double d) { return (int64_t)d; }

NVal *nval_binary(int line, int op, NVal *a, NVal *b) {
    int ta = a->tag, tb = b->tag;
    if (op == OP_AND) return nval_truthy(a) && nval_truthy(b) ? nval_yes() : nval_no();
    if (op == OP_OR)  return nval_truthy(a) || nval_truthy(b) ? nval_yes() : nval_no();
    if (op == OP_IN) return b_has(line, b, a);  /* has(collection, item) */
    if (op == OP_EQ) return nval_equals(a, b) ? nval_yes() : nval_no();
    if (op == OP_NE) return nval_equals(a, b) ? nval_no() : nval_yes();
    if (op == OP_ADD) {
        if (ta == NVAL_NUMBER && tb == NVAL_NUMBER)
            return nval_number(a->u.num + b->u.num);
        if (ta == NVAL_TEXT && tb == NVAL_TEXT) {
            sbuf sb; sb_init(&sb);
            sb_put(&sb, a->u.text.data, a->u.text.len);
            sb_put(&sb, b->u.text.data, b->u.text.len);
            NVal *t = nval_text(sb.data, sb.len);
            free(sb.data);
            return t;
        }
        niko_panic(line, "I can't add those values together.");
    }
    if (op >= OP_SUB && op <= OP_POW) {
        if (ta != NVAL_NUMBER || tb != NVAL_NUMBER)
            niko_panic(line, "I need numbers for that operation.");
        double fa = a->u.num, fb = b->u.num, r = 0;
        switch (op) {
        case OP_SUB: r = fa - fb; break;
        case OP_MUL: r = fa * fb; break;
        case OP_DIV:
            if (fb == 0.0) niko_panic(0, "You cannot divide by zero.");
            r = fa / fb; break;
        case OP_MOD: {
            /* Python semantics: sign follows the divisor */
            if (fb == 0.0) niko_panic(line, "integer division or modulo by zero");
            double q = trunc(fa / fb);
            r = fa - q * fb;
            if (r != 0.0 && ((r < 0.0) != (fb < 0.0))) r += fb;
            break;
        }
        case OP_POW: r = pow(fa, fb); break;
        }
        return nval_number(r);
    }
    /* LT LE GT GE on numbers or text */
    if (ta == NVAL_NUMBER && tb == NVAL_NUMBER) {
        int c = 0;
        switch (op) {
        case OP_LT: c = a->u.num < b->u.num; break;
        case OP_LE: c = a->u.num <= b->u.num; break;
        case OP_GT: c = a->u.num > b->u.num; break;
        default:    c = a->u.num >= b->u.num; break;
        }
        return c ? nval_yes() : nval_no();
    }
    if (ta == NVAL_TEXT && tb == NVAL_TEXT) {
        int32_t n = a->u.text.len < b->u.text.len ? a->u.text.len : b->u.text.len;
        int cmp = memcmp(a->u.text.data, b->u.text.data, (size_t)n);
        if (cmp == 0) cmp = (a->u.text.len > b->u.text.len) - (a->u.text.len < b->u.text.len);
        int c = 0;
        switch (op) {
        case OP_LT: c = cmp < 0; break;
        case OP_LE: c = cmp <= 0; break;
        case OP_GT: c = cmp > 0; break;
        default:    c = cmp >= 0; break;
        }
        return c ? nval_yes() : nval_no();
    }
    niko_panic(line, "I can't compare those values.");
}

NVal *nval_unary(int line, int op, NVal *a) {
    if (op == OP_NOT) return nval_truthy(a) ? nval_no() : nval_yes();
    if (a->tag != NVAL_NUMBER) niko_panic(0, "I can't negate that value.");
    return nval_number(-a->u.num);
}

/* ---------------- index / attr / mutation ---------------- */

int64_t nval_to_int(int line, NVal *v) {
    double d;
    if (v->tag == NVAL_NUMBER) {
        d = v->u.num;
    } else if (v->tag == NVAL_TEXT) {
        char *tmp = xmalloc((size_t)v->u.text.len + 1);
        memcpy(tmp, v->u.text.data, (size_t)v->u.text.len);
        tmp[v->u.text.len] = 0;
        char *end = NULL;
        d = strtod(tmp, &end);
        int ok = end != tmp && *end == 0;
        free(tmp);
        if (!ok || isnan(d)) niko_panic(line, "I can't use that text as a whole number.");
    } else {
        niko_panic(line, "I need a whole number for that.");
        d = 0;
    }
    if (isnan(d)) niko_panic(line, "I need a whole number for that.");
    return (int64_t)d;   /* truncates toward zero, like the WASM backend */
}

double nval_as_number(int line, NVal *v) {
    if (v->tag != NVAL_NUMBER) niko_panic(line, "I need a number for that.");
    return v->u.num;
}

NVal *nval_index_get(int line, NVal *obj, NVal *idx) {
    if (obj->tag == NVAL_LIST) {
        /* 1-based; negative wraps like the VM */
        int64_t i = nval_to_int(line, idx) - 1;
        int64_t n = obj->u.list.len;
        if (i < 0) i += n;
        if (i < 0 || i >= n) niko_panic(line, "list index out of range");
        return obj->u.list.items[i];
    }
    if (obj->tag == NVAL_TEXT) {
        /* 0-based like the VM */
        int64_t ci = nval_to_int(line, idx);
        int32_t nchars = utf8_len(obj->u.text.data, obj->u.text.len);
        if (ci < 0) ci += nchars;
        if (ci < 0 || ci >= nchars) niko_panic(line, "string index out of range");
        int32_t bo = utf8_byte_offset(obj->u.text.data, obj->u.text.len, (int32_t)ci);
        int32_t eo = utf8_byte_offset(obj->u.text.data, obj->u.text.len, (int32_t)ci + 1);
        return nval_text(obj->u.text.data + bo, eo - bo);
    }
    if (obj->tag == NVAL_RECORD) {
        if (idx->tag != NVAL_TEXT) niko_panic(line, "I can't use that as a key.");
        int i = rec_find(obj, idx->u.text.data, idx->u.text.len);
        if (i < 0) {
            /* mirror Python's KeyError: 'key' */
            sbuf b; sb_init(&b);
            sb_ch(&b, '\'');
            sb_put(&b, idx->u.text.data, idx->u.text.len);
            sb_ch(&b, '\'');
            char *m = xmalloc((size_t)b.len + 1);
            memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
            free(b.data);
            niko_panic(line, m);
        }
        return obj->u.rec.vals[i];
    }
    niko_panic(line, "I can't index into that value.");
}

void nval_index_set(int line, NVal *obj, NVal *idx, NVal *v) {
    if (obj->tag == NVAL_LIST) {
        int64_t i = nval_to_int(line, idx) - 1;
        int64_t n = obj->u.list.len;
        if (i < 0) i += n;
        if (i < 0 || i >= n) niko_panic(line, "list index out of range");
        obj->u.list.items[i] = v;
        return;
    }
    if (obj->tag == NVAL_RECORD) {
        if (idx->tag != NVAL_TEXT) niko_panic(line, "I can't use that as a key.");
        nval_record_set(obj, idx->u.text.data, idx->u.text.len, v);
        return;
    }
    niko_panic(line, "I can't store into that value.");
}

NVal *nval_attr(int line, NVal *obj, const char *name, int32_t nlen) {
    if (obj->tag != NVAL_RECORD)
        niko_panic(line, "I can't get that attribute.");
    int i = rec_find(obj, name, nlen);
    if (i < 0) {
        sbuf b; sb_init(&b);
        sb_ch(&b, '\'');
        sb_put(&b, name, nlen);
        sb_ch(&b, '\'');
        char *m = xmalloc((size_t)b.len + 1);
        memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
        free(b.data);
        niko_panic(line, m);
    }
    return obj->u.rec.vals[i];
}

void nval_put_in(int line, NVal *target, NVal *v) {
    /* `put v in target`: list -> append, record -> merge keys */
    if (target->tag == NVAL_LIST) { nval_list_push(target, v); return; }
    if (target->tag == NVAL_RECORD && v->tag == NVAL_RECORD) {
        for (int32_t i = 0; i < v->u.rec.len; i++)
            nval_record_set(target, v->u.rec.keys[i], v->u.rec.klen[i],
                            v->u.rec.vals[i]);
        return;
    }
    niko_panic(line, "I can't put that there.");
}

void nval_list_remove(int line, NVal *target, NVal *v) {
    (void)line;
    if (target->tag != NVAL_LIST) return;
    for (int32_t i = 0; i < target->u.list.len; i++) {
        if (nval_equals(target->u.list.items[i], v)) {
            for (int32_t j = i; j + 1 < target->u.list.len; j++)
                target->u.list.items[j] = target->u.list.items[j + 1];
            target->u.list.len--;
            return;
        }
    }
}

/* for-each iteration: list -> itself, text -> chars, record -> keys */
NVal *nval_to_iter_list(int line, NVal *v) {
    if (v->tag == NVAL_LIST) return v;
    NVal *out = nval_list();
    if (v->tag == NVAL_TEXT) {
        int32_t nchars = utf8_len(v->u.text.data, v->u.text.len);
        for (int32_t k = 0; k < nchars; k++) {
            int32_t bo = utf8_byte_offset(v->u.text.data, v->u.text.len, k);
            int32_t eo = utf8_byte_offset(v->u.text.data, v->u.text.len, k + 1);
            nval_list_push(out, nval_text(v->u.text.data + bo, eo - bo));
        }
        return out;
    }
    if (v->tag == NVAL_RECORD) {
        for (int32_t i = 0; i < v->u.rec.len; i++)
            nval_list_push(out, nval_text(v->u.rec.keys[i], v->u.rec.klen[i]));
        return out;
    }
    niko_panic(line, "I can't loop over that value.");
}

/* ---------------- effects: say / ask ---------------- */

void niko_say(int argc, NVal **argv) {
    for (int i = 0; i < argc; i++) {
        if (i) putchar(' ');
        NVal *t = nval_to_text(argv[i]);
        fwrite(t->u.text.data, 1, (size_t)t->u.text.len, stdout);
    }
    putchar('\n');
    fflush(stdout);
}

static NVal *ask_read_line(void) {
    sbuf b; sb_init(&b);
    int c;
    while ((c = getchar()) != EOF && c != '\n') sb_ch(&b, (char)c);
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return t;
}

static double parse_num_str(const char *s, int32_t len, int *ok) {
    char *tmp = xmalloc((size_t)len + 1);
    memcpy(tmp, s, (size_t)len);
    tmp[len] = 0;
    char *end = NULL;
    double d = strtod(tmp, &end);
    *ok = end != tmp && *end == 0 && !isnan(d);
    free(tmp);
    return d;
}

NVal *niko_ask(const char *prompt, int32_t plen, int want_number) {
    fwrite(prompt, 1, (size_t)plen, stdout);
    fflush(stdout);
    for (;;) {
        NVal *t = ask_read_line();
        if (!want_number) return t;
        int ok = 0;
        double d = parse_num_str(t->u.text.data, t->u.text.len, &ok);
        if (ok) return nval_number(d);
        printf("Please type a number.\n");
        fflush(stdout);
    }
}

/* ---------------- text builtins ---------------- */

static NVal *need_text(int line, NVal *x, const char *what) {
    if (x->tag != NVAL_TEXT) niko_panic(line, what);
    return x;
}

NVal *b_length(int line, NVal *x) {
    if (x->tag == NVAL_TEXT) return nval_number((double)utf8_len(x->u.text.data, x->u.text.len));
    if (x->tag == NVAL_LIST) return nval_number((double)x->u.list.len);
    if (x->tag == NVAL_RECORD) return nval_number((double)x->u.rec.len);
    niko_panic(line, "I can't measure that value.");
}

NVal *b_text(int line, NVal *x) {
    (void)line;
    return nval_to_text(x);
}

NVal *b_number(int line, NVal *x) {
    if (x->tag == NVAL_NUMBER)
        return nval_number((double)(int64_t)x->u.num);  /* int(x): truncates */
    if (x->tag == NVAL_YESNO) return nval_number((double)x->u.yn);
    if (x->tag == NVAL_TEXT) {
        int ok = 0;
        double d = parse_num_str(x->u.text.data, x->u.text.len, &ok);
        if (!ok) {
            /* "I can't turn '<s>' into a number." — exact VM message */
            sbuf b; sb_init(&b);
            sb_put(&b, "I can't turn '", 14);
            sb_put(&b, x->u.text.data, x->u.text.len);
            sb_put(&b, "' into a number.", 17);
            char *m = xmalloc((size_t)b.len + 1);
            memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
            free(b.data);
            niko_panic(0, m);
        }
        return nval_number(d);
    }
    /* "I can't turn <repr> into a number." — fmt() form */
    NVal *r = nval_to_text(x);
    sbuf b; sb_init(&b);
    sb_put(&b, "I can't turn ", 13);
    sb_put(&b, r->u.text.data, r->u.text.len);
    sb_put(&b, " into a number.", 15);
    char *m = xmalloc((size_t)b.len + 1);
    memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
    free(b.data);
    niko_panic(0, m);
}

NVal *b_item_of(int line, NVal *i, NVal *x) {
    /* 1-based for lists and text; negative wraps */
    int64_t j = nval_to_int(line, i) - 1;
    if (x->tag == NVAL_LIST) {
        int64_t n = x->u.list.len;
        if (j < 0) j += n;
        if (j < 0 || j >= n) niko_panic(line, "list index out of range");
        return x->u.list.items[j];
    }
    if (x->tag == NVAL_TEXT) {
        int32_t nchars = utf8_len(x->u.text.data, x->u.text.len);
        int64_t k = j;
        if (k < 0) k += nchars;
        if (k < 0 || k >= nchars) niko_panic(line, "string index out of range");
        int32_t bo = utf8_byte_offset(x->u.text.data, x->u.text.len, (int32_t)k);
        int32_t eo = utf8_byte_offset(x->u.text.data, x->u.text.len, (int32_t)k + 1);
        return nval_text(x->u.text.data + bo, eo - bo);
    }
    niko_panic(line, "I can't pick that out of that value.");
}

static NVal *b_case(int line, NVal *x, int upper) {
    /* ASCII-only case mapping, like the WASM backend */
    need_text(line, x, "I can only change the case of text.");
    NVal *t = nval_text(x->u.text.data, x->u.text.len);
    for (int32_t i = 0; i < t->u.text.len; i++) {
        unsigned char c = (unsigned char)t->u.text.data[i];
        if (upper && c >= 'a' && c <= 'z') t->u.text.data[i] = (char)(c - 32);
        if (!upper && c >= 'A' && c <= 'Z') t->u.text.data[i] = (char)(c + 32);
    }
    return t;
}

NVal *b_upper(int line, NVal *x) { return b_case(line, x, 1); }
NVal *b_lower(int line, NVal *x) { return b_case(line, x, 0); }

NVal *b_trim(int line, NVal *x) {
    need_text(line, x, "I can only trim text.");
    const char *s = x->u.text.data;
    int32_t n = x->u.text.len, a = 0, b = n;
    while (a < b && (s[a] == ' ' || s[a] == '\t' || s[a] == '\n' || s[a] == '\r')) a++;
    while (b > a && (s[b-1] == ' ' || s[b-1] == '\t' || s[b-1] == '\n' || s[b-1] == '\r')) b--;
    return nval_text(s + a, b - a);
}

/* byte index of needle in haystack, or -1 */
static int32_t text_find(const char *h, int32_t hn, const char *n, int32_t nn) {
    if (nn == 0) return 0;
    for (int32_t i = 0; i + nn <= hn; i++)
        if (memcmp(h + i, n, (size_t)nn) == 0) return i;
    return -1;
}

NVal *b_replace(int line, NVal *x, NVal *o, NVal *nw) {
    need_text(line, x, "I can only replace inside text.");
    need_text(line, o, "I can only replace text with text.");
    need_text(line, nw, "I can only replace text with text.");
    sbuf b; sb_init(&b);
    const char *s = x->u.text.data;
    int32_t n = x->u.text.len, on = o->u.text.len, i = 0;
    if (on == 0) { sb_put(&b, s, n); }
    else {
        while (i < n) {
            int32_t f = text_find(s + i, n - i, o->u.text.data, on);
            if (f < 0) break;
            sb_put(&b, s + i, f);
            sb_put(&b, nw->u.text.data, nw->u.text.len);
            i += f + on;
        }
        sb_put(&b, s + i, n - i);
    }
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return t;
}

NVal *b_split(int line, NVal *x, NVal *sep) {
    need_text(line, x, "I can only split text.");
    need_text(line, sep, "I can only split on text.");
    NVal *out = nval_list();
    const char *s = x->u.text.data;
    int32_t n = x->u.text.len, sn = sep->u.text.len, i = 0;
    if (sn == 0) {
        /* split into characters */
        int32_t nchars = utf8_len(s, n);
        for (int32_t k = 0; k < nchars; k++) {
            int32_t bo = utf8_byte_offset(s, n, k), eo = utf8_byte_offset(s, n, k + 1);
            nval_list_push(out, nval_text(s + bo, eo - bo));
        }
        return out;
    }
    while (i <= n) {
        int32_t f = text_find(s + i, n - i, sep->u.text.data, sn);
        if (f < 0) { nval_list_push(out, nval_text(s + i, n - i)); break; }
        nval_list_push(out, nval_text(s + i, f));
        i += f + sn;
    }
    return out;
}

NVal *b_join(int line, NVal *x, NVal *sep) {
    if (x->tag != NVAL_LIST) niko_panic(line, "I can only join a list.");
    need_text(line, sep, "I can only join with text.");
    sbuf b; sb_init(&b);
    for (int32_t i = 0; i < x->u.list.len; i++) {
        NVal *e = x->u.list.items[i];
        if (e->tag != NVAL_TEXT) niko_panic(line, "I can only join text.");
        if (i) sb_put(&b, sep->u.text.data, sep->u.text.len);
        sb_put(&b, e->u.text.data, e->u.text.len);
    }
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return t;
}

NVal *b_has(int line, NVal *c, NVal *x) {
    /* has(collection, item): mirrors WASM in_op(line, item, collection) */
    if (c->tag == NVAL_TEXT) {
        if (x->tag != NVAL_TEXT) niko_panic(line, "I can't look for that inside text.");
        return text_find(c->u.text.data, c->u.text.len,
                         x->u.text.data, x->u.text.len) >= 0 ? nval_yes() : nval_no();
    }
    if (c->tag == NVAL_LIST) {
        for (int32_t i = 0; i < c->u.list.len; i++)
            if (nval_equals(x, c->u.list.items[i])) return nval_yes();
        return nval_no();
    }
    if (c->tag == NVAL_RECORD) {
        if (x->tag != NVAL_TEXT) return nval_no();
        return rec_find(c, x->u.text.data, x->u.text.len) >= 0 ? nval_yes() : nval_no();
    }
    niko_panic(line, "I can't look inside that value.");
}

NVal *b_starts_with(int line, NVal *x, NVal *p) {
    need_text(line, x, "I need text for that.");
    need_text(line, p, "I need text for that.");
    int ok = x->u.text.len >= p->u.text.len &&
             memcmp(x->u.text.data, p->u.text.data, (size_t)p->u.text.len) == 0;
    return ok ? nval_yes() : nval_no();
}

NVal *b_ends_with(int line, NVal *x, NVal *p) {
    need_text(line, x, "I need text for that.");
    need_text(line, p, "I need text for that.");
    int32_t n = x->u.text.len, m = p->u.text.len;
    int ok = n >= m && memcmp(x->u.text.data + n - m, p->u.text.data, (size_t)m) == 0;
    return ok ? nval_yes() : nval_no();
}

NVal *b_count_of(int line, NVal *x, NVal *n) {
    if (x->tag == NVAL_TEXT && n->tag == NVAL_TEXT) {
        const char *s = x->u.text.data, *nd = n->u.text.data;
        int32_t sl = x->u.text.len, nl = n->u.text.len;
        if (nl == 0)
            return nval_number((double)(utf8_len(s, sl) + 1));  /* Python parity */
        int64_t cnt = 0;
        int32_t i = 0;
        while (i + nl <= sl) {
            int32_t f = text_find(s + i, sl - i, nd, nl);
            if (f < 0) break;
            cnt++;
            i += f + nl;
        }
        return nval_number((double)cnt);
    }
    if (x->tag == NVAL_LIST) {
        int64_t cnt = 0;
        for (int32_t i = 0; i < x->u.list.len; i++)
            if (nval_equals(x->u.list.items[i], n)) cnt++;
        return nval_number((double)cnt);
    }
    niko_panic(line, "I can't count that.");
}

/* ---------------- math builtins ---------------- */

static double need_number(int line, NVal *x) {
    if (x->tag != NVAL_NUMBER) niko_panic(0, "I need a number for that.");
    (void)line;
    return x->u.num;
}

NVal *b_abs(int line, NVal *x) { return nval_number(fabs(need_number(line, x))); }
NVal *b_ceil(int line, NVal *x) { return nval_number(ceil(need_number(line, x))); }
NVal *b_floor(int line, NVal *x) { return nval_number(floor(need_number(line, x))); }
NVal *b_round(int line, NVal *x) { return nval_number(nearbyint(need_number(line, x))); }
NVal *b_sqrt(int line, NVal *x) {
    double d = need_number(line, x);
    if (d < 0) niko_panic(0, "math domain error");
    return nval_number(sqrt(d));
}

static NVal *need_num_list(int line, NVal *x) {
    if (x->tag != NVAL_LIST) niko_panic(0, "I need a list for that.");
    (void)line;
    return x;
}

NVal *b_sum(int line, NVal *x) {
    need_num_list(line, x);
    double tot = 0;
    for (int32_t i = 0; i < x->u.list.len; i++) {
        NVal *e = x->u.list.items[i];
        if (e->tag != NVAL_NUMBER) niko_panic(0, "I can only total up numbers.");
        tot += e->u.num;
    }
    return nval_number(tot);
}

NVal *b_average(int line, NVal *x) {
    need_num_list(line, x);
    if (x->u.list.len == 0) niko_panic(0, "division by zero");
    double tot = 0;
    for (int32_t i = 0; i < x->u.list.len; i++) {
        NVal *e = x->u.list.items[i];
        if (e->tag != NVAL_NUMBER) niko_panic(0, "I can only total up numbers.");
        tot += e->u.num;
    }
    return nval_number(tot / x->u.list.len);
}

/* max/min: single list arg (the codegen packs variadic args into a list) */
static NVal *b_max_min(int line, NVal *x, int want_max) {
    if (x->tag != NVAL_LIST) niko_panic(0, "I need a list for that.");
    int32_t n = x->u.list.len;
    if (n == 0) niko_panic(0, want_max ? "max() arg is an empty sequence"
                                       : "min() arg is an empty sequence");
    NVal *best = x->u.list.items[0];
    int mode = -1;  /* 0 numbers, 1 text */
    if (best->tag == NVAL_NUMBER) mode = 0;
    else if (best->tag == NVAL_TEXT) mode = 1;
    else niko_panic(0, "I can't compare those values.");
    for (int32_t i = 1; i < n; i++) {
        NVal *e = x->u.list.items[i];
        int better = 0;
        if (mode == 0) {
            if (e->tag != NVAL_NUMBER) niko_panic(0, "I can't compare those values.");
            better = want_max ? e->u.num > best->u.num : e->u.num < best->u.num;
        } else {
            if (e->tag != NVAL_TEXT) niko_panic(0, "I can't compare those values.");
            int32_t m = e->u.text.len < best->u.text.len ? e->u.text.len : best->u.text.len;
            int cmp = memcmp(e->u.text.data, best->u.text.data, (size_t)m);
            if (cmp == 0)
                cmp = (e->u.text.len > best->u.text.len) - (e->u.text.len < best->u.text.len);
            better = want_max ? cmp > 0 : cmp < 0;
        }
        if (better) best = e;
    }
    (void)line;
    return best;
}

NVal *b_max(int line, NVal *x) { return b_max_min(line, x, 1); }
NVal *b_min(int line, NVal *x) { return b_max_min(line, x, 0); }

/* ---------------- list builtins ---------------- */

NVal *b_sorted(int line, NVal *x) {
    if (x->tag != NVAL_LIST) niko_panic(0, "I can only sort a list.");
    int32_t n = x->u.list.len;
    NVal *out = nval_list();
    for (int32_t i = 0; i < n; i++) nval_list_push(out, x->u.list.items[i]);
    if (n == 0) return out;
    int mode = -1;
    if (out->u.list.items[0]->tag == NVAL_NUMBER) mode = 0;
    else if (out->u.list.items[0]->tag == NVAL_TEXT) mode = 1;
    else niko_panic(0, "I can't sort those values.");
    /* insertion sort, like the WASM backend */
    for (int32_t i = 1; i < n; i++) {
        NVal *v = out->u.list.items[i];
        int32_t j = i;
        while (j > 0) {
            NVal *u = out->u.list.items[j - 1];
            int less;
            if (mode == 0) {
                if (v->tag != NVAL_NUMBER || u->tag != NVAL_NUMBER)
                    niko_panic(0, "I can't sort those values.");
                less = v->u.num < u->u.num;
            } else {
                if (v->tag != NVAL_TEXT || u->tag != NVAL_TEXT)
                    niko_panic(0, "I can't sort those values.");
                int32_t m = v->u.text.len < u->u.text.len ? v->u.text.len : u->u.text.len;
                int cmp = memcmp(v->u.text.data, u->u.text.data, (size_t)m);
                if (cmp == 0)
                    cmp = (v->u.text.len > u->u.text.len) - (v->u.text.len < u->u.text.len);
                less = cmp < 0;
            }
            if (!less) break;
            out->u.list.items[j] = u;
            j--;
        }
        out->u.list.items[j] = v;
    }
    (void)line;
    return out;
}

NVal *b_reversed(int line, NVal *x) {
    if (x->tag == NVAL_LIST) {
        NVal *out = nval_list();
        for (int32_t i = x->u.list.len - 1; i >= 0; i--)
            nval_list_push(out, x->u.list.items[i]);
        return out;
    }
    if (x->tag == NVAL_TEXT) {
        /* reversed text -> list of 1-char texts (VM parity) */
        NVal *out = nval_list();
        int32_t nchars = utf8_len(x->u.text.data, x->u.text.len);
        for (int32_t k = nchars - 1; k >= 0; k--) {
            int32_t bo = utf8_byte_offset(x->u.text.data, x->u.text.len, k);
            int32_t eo = utf8_byte_offset(x->u.text.data, x->u.text.len, k + 1);
            nval_list_push(out, nval_text(x->u.text.data + bo, eo - bo));
        }
        return out;
    }
    niko_panic(line, "I can only reverse a list or text.");
}

NVal *b_unique(int line, NVal *x) {
    if (x->tag != NVAL_LIST) niko_panic(line, "I can only dedupe a list.");
    NVal *out = nval_list();
    for (int32_t i = 0; i < x->u.list.len; i++) {
        NVal *e = x->u.list.items[i];
        int seen = 0;
        for (int32_t j = 0; j < out->u.list.len; j++)
            if (nval_equals(e, out->u.list.items[j])) { seen = 1; break; }
        if (!seen) nval_list_push(out, e);
    }
    return out;
}

NVal *b_keys(int line, NVal *x) {
    if (x->tag != NVAL_RECORD) niko_panic(line, "I can only list the keys of a record.");
    NVal *out = nval_list();
    for (int32_t i = 0; i < x->u.rec.len; i++)
        nval_list_push(out, nval_text(x->u.rec.keys[i], x->u.rec.klen[i]));
    return out;
}

NVal *b_niko_range(int line, NVal *a, NVal *b) {
    int64_t lo = nval_to_int(line, a), hi = nval_to_int(line, b);
    NVal *out = nval_list();
    if (lo <= hi) {
        for (int64_t i = lo; i <= hi; i++) nval_list_push(out, nval_number((double)i));
    } else {
        for (int64_t i = lo; i >= hi; i--) nval_list_push(out, nval_number((double)i));
    }
    return out;
}

/* ---------------- ok/error results ---------------- */

NVal *b_ok(int line, NVal *x) { (void)line; return nval_ok(x); }

NVal *b_error(int line, NVal *x) {
    (void)line;
    return nval_error(nval_to_text(x));  /* message uses fmt(), like the VM */
}

NVal *b_is_ok(int line, NVal *x) {
    (void)line;
    return (x->tag == NVAL_RESULT && x->u.res.ok) ? nval_yes() : nval_no();
}

NVal *b_is_error(int line, NVal *x) {
    (void)line;
    return (x->tag == NVAL_RESULT && !x->u.res.ok) ? nval_yes() : nval_no();
}

NVal *b_unwrap(int line, NVal *x) {
    (void)line;
    if (x->tag == NVAL_NOTHING) niko_panic(0, "tried to unwrap nothing");
    if (x->tag != NVAL_RESULT) return x;
    if (x->u.res.ok) return x->u.res.val;
    NVal *m = x->u.res.val;
    char *c = xmalloc((size_t)m->u.text.len + 1);
    memcpy(c, m->u.text.data, (size_t)m->u.text.len);
    c[m->u.text.len] = 0;
    niko_panic(0, c);
}

NVal *b_unwrap_or(int line, NVal *x, NVal *d) {
    (void)line;
    if (x->tag == NVAL_NOTHING) return d;
    if (x->tag != NVAL_RESULT) return x;
    return x->u.res.ok ? x->u.res.val : d;
}

NVal *b_error_message(int line, NVal *x) {
    (void)line;
    if (x->tag == NVAL_RESULT && !x->u.res.ok) return x->u.res.val;
    return nval_text("", 0);
}

NVal *b_try_number(int line, NVal *x) {
    if (x->tag == NVAL_NUMBER)
        return nval_ok(nval_number((double)(int64_t)x->u.num));
    if (x->tag == NVAL_YESNO)
        return nval_ok(nval_number((double)x->u.yn));
    if (x->tag == NVAL_TEXT) {
        int ok = 0;
        double d = parse_num_str(x->u.text.data, x->u.text.len, &ok);
        if (ok) return nval_ok(nval_number(d));
        return nval_error(x);  /* WASM parity: the text itself is the message */
    }
    NVal *r = nval_to_text(x);
    sbuf b; sb_init(&b);
    sb_put(&b, "I can't turn ", 13);
    sb_put(&b, r->u.text.data, r->u.text.len);
    sb_put(&b, " into a number.", 15);
    NVal *m = nval_text(b.data, b.len);
    free(b.data);
    (void)line;
    return nval_error(m);
}

/* ---------------- random / time ---------------- */

static int rand_seeded = 0;

static void rand_seed_once(void) {
    if (!rand_seeded) {
        struct timeval tv;
        gettimeofday(&tv, NULL);
        srand((unsigned)(tv.tv_sec ^ tv.tv_usec ^ (uintptr_t)&rand_seeded));
        rand_seeded = 1;
    }
}

/* inclusive i32 range, like the WASM host import */
static int64_t rand_i32(int64_t lo, int64_t hi) {
    rand_seed_once();
    uint64_t span = (uint64_t)(hi - lo) + 1;
    uint64_t r = ((uint64_t)rand() << 32) | (uint64_t)rand();
    return lo + (int64_t)(r % span);
}

NVal *b_random_int(int line, NVal *a, NVal *b) {
    int64_t lo = nval_to_int(line, a), hi = nval_to_int(line, b);
    if (lo > hi) niko_panic(0, "empty range for randrange()");
    return nval_number((double)rand_i32(lo, hi));
}

NVal *b_pick(int line, NVal *x) {
    if (x->tag == NVAL_LIST) {
        if (x->u.list.len == 0) niko_panic(0, "I can't pick from an empty list.");
        return x->u.list.items[rand_i32(0, x->u.list.len - 1)];
    }
    if (x->tag == NVAL_TEXT) {
        int32_t nchars = utf8_len(x->u.text.data, x->u.text.len);
        if (nchars == 0) niko_panic(0, "I can't pick from an empty list.");
        int64_t k = rand_i32(0, nchars - 1);
        int32_t bo = utf8_byte_offset(x->u.text.data, x->u.text.len, (int32_t)k);
        int32_t eo = utf8_byte_offset(x->u.text.data, x->u.text.len, (int32_t)k + 1);
        return nval_text(x->u.text.data + bo, eo - bo);
    }
    niko_panic(0, "I can't pick from that value.");
}

NVal *b_today(int line) {
    (void)line;
    time_t t = time(NULL);
    struct tm tm;
    localtime_r(&t, &tm);
    char buf[16];
    strftime(buf, sizeof buf, "%Y-%m-%d", &tm);
    return nval_text_cstr(buf);
}

NVal *b_now(int line) {
    (void)line;
    struct timeval tv;
    gettimeofday(&tv, NULL);
    struct tm tm;
    localtime_r(&tv.tv_sec, &tm);
    char buf[64];
    snprintf(buf, sizeof buf, "%04d-%02d-%02dT%02d:%02d:%02d.%06d",
             tm.tm_year + 1900, tm.tm_mon + 1, tm.tm_mday,
             tm.tm_hour, tm.tm_min, tm.tm_sec, (int)tv.tv_usec);
    return nval_text_cstr(buf);
}

NVal *b_sleep(int line, NVal *x) {
    double d = nval_as_number(line, x);
    if (d > 0) {
        struct timespec ts;
        ts.tv_sec = (time_t)d;
        ts.tv_nsec = (long)((d - floor(d)) * 1e9);
        nanosleep(&ts, NULL);
    }
    return nval_nothing();
}

/* ---------------- file I/O (cwd-relative, like the VM) ---------------- */

static char *path_cstr(int line, NVal *p) {
    NVal *t = nval_to_text(p);
    char *c = xmalloc((size_t)t->u.text.len + 1);
    memcpy(c, t->u.text.data, (size_t)t->u.text.len);
    c[t->u.text.len] = 0;
    (void)line;
    return c;
}

NVal *b_write_file(int line, NVal *p, NVal *t) {
    char *path = path_cstr(line, p);
    NVal *txt = nval_to_text(t);
    FILE *f = fopen(path, "w");
    if (!f) {
        sbuf b; sb_init(&b);
        sb_put(&b, "I couldn't write the file \"", 27);
        sb_put(&b, path, (int32_t)strlen(path));
        sb_put(&b, "\".", 2);
        char *m = xmalloc((size_t)b.len + 1);
        memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
        free(b.data); free(path);
        niko_panic(line, m);
    }
    fwrite(txt->u.text.data, 1, (size_t)txt->u.text.len, f);
    fclose(f);
    free(path);
    return nval_nothing();
}

NVal *b_append_file(int line, NVal *p, NVal *t) {
    char *path = path_cstr(line, p);
    NVal *txt = nval_to_text(t);
    FILE *f = fopen(path, "a");
    if (!f) {
        free(path);
        niko_panic(line, "I couldn't open that file.");
    }
    fwrite(txt->u.text.data, 1, (size_t)txt->u.text.len, f);
    fclose(f);
    free(path);
    return nval_nothing();
}

static NVal *read_file_bytes(int line, NVal *p, const char *missing_msg) {
    char *path = path_cstr(line, p);
    FILE *f = fopen(path, "rb");
    if (!f) {
        sbuf b; sb_init(&b);
        sb_put(&b, missing_msg, (int32_t)strlen(missing_msg));
        sb_put(&b, " \"", 2);
        sb_put(&b, path, (int32_t)strlen(path));
        sb_put(&b, "\".", 2);
        char *m = xmalloc((size_t)b.len + 1);
        memcpy(m, b.data, (size_t)b.len); m[b.len] = 0;
        free(b.data); free(path);
        niko_panic(line, m);
    }
    sbuf b; sb_init(&b);
    char chunk[4096];
    size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) sb_put(&b, chunk, (int32_t)n);
    fclose(f);
    free(path);
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return t;
}

NVal *b_read_file(int line, NVal *p) {
    return read_file_bytes(line, p, "I couldn't find the file");
}

NVal *b_read_lines(int line, NVal *p) {
    NVal *t = read_file_bytes(line, p, "I couldn't find the file");
    NVal *out = nval_list();
    const char *s = t->u.text.data;
    int32_t n = t->u.text.len, i = 0;
    while (i < n) {
        int32_t j = i;
        while (j < n && s[j] != '\n') j++;
        int32_t e = j;
        if (e > i && s[e-1] == '\r') e--;
        nval_list_push(out, nval_text(s + i, e - i));
        i = j + 1;
    }
    return out;
}

NVal *b_file_exists(int line, NVal *p) {
    char *path = path_cstr(line, p);
    FILE *f = fopen(path, "rb");
    if (f) fclose(f);
    free(path);
    (void)line;
    return f ? nval_yes() : nval_no();
}

NVal *b_try_read_file(int line, NVal *p) {
    char *path = path_cstr(line, p);
    FILE *f = fopen(path, "rb");
    if (!f) {
        sbuf b; sb_init(&b);
        sb_put(&b, "I couldn't find the file \"", 26);
        sb_put(&b, path, (int32_t)strlen(path));
        sb_put(&b, "\".", 2);
        NVal *m = nval_text(b.data, b.len);
        free(b.data); free(path);
        (void)line;
        return nval_error(m);
    }
    sbuf b; sb_init(&b);
    char chunk[4096];
    size_t n;
    while ((n = fread(chunk, 1, sizeof chunk, f)) > 0) sb_put(&b, chunk, (int32_t)n);
    fclose(f);
    free(path);
    NVal *t = nval_text(b.data, b.len);
    free(b.data);
    return nval_ok(t);
}

/* ---------------- small accessors for generated code ---------------- */

int32_t nval_list_len(NVal *l) { return l->u.list.len; }
NVal *nval_list_item(NVal *l, int64_t i) { return l->u.list.items[i]; }
NVal *nval_list_slice(NVal *l, int64_t from) {
    NVal *out = nval_list();
    int64_t start = from - 1;
    if (start < 0) start = 0;
    for (int64_t i = start; i < l->u.list.len; i++)
        nval_list_push(out, l->u.list.items[i]);
    return out;
}

NVal *niko_ask_val(NVal *prompt, int want_number) {
    NVal *t = nval_to_text(prompt);
    return niko_ask(t->u.text.data, t->u.text.len, want_number);
}
