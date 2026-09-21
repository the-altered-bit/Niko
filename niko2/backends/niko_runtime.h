/* Niko native runtime — public API (Alpha 10).
 *
 * Every Niko value is a heap-allocated NVal*. The code generator in
 * native.py emits C that only ever touches NVal* through these functions,
 * so the GC-less, malloc-everything strategy below can be replaced later
 * without touching generated code.
 */
#ifndef NIKO_RUNTIME_H
#define NIKO_RUNTIME_H

#include <stdint.h>

typedef struct NVal NVal;

enum {
    NVAL_NOTHING = 0,
    NVAL_NUMBER  = 1,
    NVAL_TEXT    = 2,
    NVAL_YESNO   = 3,
    NVAL_LIST    = 4,
    NVAL_RECORD  = 5,
    NVAL_RESULT  = 6,
    NVAL_FUNCTION = 7   /* first-class function: {func_id, name, env} */
};

/* Binary/unary op codes (mirror the WASM backend). */
enum {
    OP_ADD = 0, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_POW,
    OP_EQ, OP_NE, OP_LT, OP_LE, OP_GT, OP_GE,
    OP_AND, OP_OR, OP_IN,
    OP_NOT = 0, OP_NEG = 1
};

struct NVal {
    int32_t tag;
    union {
        double num;
        int32_t yn;                                   /* yesno payload: 1/0 */
        struct { char *data; int32_t len; } text;      /* UTF-8 bytes */
        struct { NVal **items; int32_t len, cap; } list;
        struct { char **keys; int32_t *klen;
                 NVal **vals; int32_t len, cap; } rec;
        struct { int32_t ok; NVal *val; } res;         /* error val is a text */
        struct { int32_t func_id;                      /* niko_call_dispatch id */
                 char *name; int32_t nlen;             /* simple source name */
                 NVal *env; } fn;                      /* list of cell values */
    } u;
};

/* -- construction -------------------------------------------------- */
NVal *nval_nothing(void);                 /* singletons: never free */
NVal *nval_yes(void);
NVal *nval_no(void);
NVal *nval_number(double d);
NVal *nval_text(const char *s, int32_t len);   /* copies bytes */
NVal *nval_text_cstr(const char *s);
NVal *nval_list(void);
void  nval_list_push(NVal *l, NVal *v);
NVal *nval_record(void);
void  nval_record_set(NVal *r, const char *k, int32_t klen, NVal *v);
NVal *nval_ok(NVal *v);
NVal *nval_error(NVal *msg_text);          /* msg must be a text value */
NVal *nval_function(int32_t id, const char *name, int32_t nlen, NVal *env);
/* A cell is a 1-element list used as a shared mutable box for closures:
 * captured variables are read with nval_cell_get and written with
 * nval_cell_set, so every holder sees the same current value. */
NVal *nval_cell(NVal *v);
NVal *nval_cell_get(NVal *c);
void  nval_cell_set(NVal *c, NVal *v);

/* -- core ops (line = Niko source line, for panic messages) -------- */
_Noreturn void niko_panic(int line, const char *msg);
_Noreturn void niko_arity_panic(const char *name, int want, int got);
/* Call a Niko function value: checks the tag (panics
 * "I can't call <value> as a function." otherwise) and dispatches through
 * niko_call_dispatch, which the generated program defines. */
NVal *niko_call(int line, NVal *fn, int nargs, NVal **args);
NVal *niko_call_dispatch(int line, int32_t func_id, NVal *env,
                         int nargs, NVal **args);   /* generated code */
int   nval_truthy(NVal *v);
int   nval_equals(NVal *a, NVal *b);       /* Python-like: 1 == yes */
NVal *nval_binary(int line, int op, NVal *a, NVal *b);
NVal *nval_unary(int line, int op, NVal *a);
NVal *nval_to_text(NVal *v);               /* fresh text, fmt() semantics */
NVal *nval_index_get(int line, NVal *obj, NVal *idx);
void  nval_index_set(int line, NVal *obj, NVal *idx, NVal *v);
NVal *nval_attr(int line, NVal *obj, const char *name, int32_t nlen);
void  nval_put_in(int line, NVal *target, NVal *v);
void  nval_list_remove(int line, NVal *target, NVal *v);
NVal *nval_to_iter_list(int line, NVal *v);/* list/text/record -> fresh list */
int64_t nval_to_int(int line, NVal *v);
double  nval_as_number(int line, NVal *v);

/* -- effects -------------------------------------------------------- */
void  niko_say(int argc, NVal **argv);    /* space-joined, trailing \n */
NVal *niko_ask(const char *prompt, int32_t plen, int want_number);
NVal *niko_ask_val(NVal *prompt, int want_number);
int32_t nval_list_len(NVal *l);
NVal *nval_list_item(NVal *l, int64_t i);

/* -- builtins: every one takes the Niko source line first ----------- */
NVal *b_length(int line, NVal *x);
NVal *b_text(int line, NVal *x);
NVal *b_number(int line, NVal *x);
NVal *b_item_of(int line, NVal *i, NVal *x);
NVal *b_upper(int line, NVal *x);
NVal *b_lower(int line, NVal *x);
NVal *b_trim(int line, NVal *x);
NVal *b_replace(int line, NVal *x, NVal *old, NVal *new_);
NVal *b_split(int line, NVal *x, NVal *sep);
NVal *b_join(int line, NVal *x, NVal *sep);
NVal *b_has(int line, NVal *x, NVal *v);
NVal *b_sum(int line, NVal *x);
NVal *b_average(int line, NVal *x);
NVal *b_abs(int line, NVal *x);
NVal *b_ceil(int line, NVal *x);
NVal *b_floor(int line, NVal *x);
NVal *b_round(int line, NVal *x);
NVal *b_sqrt(int line, NVal *x);
NVal *b_max(int line, NVal *x);
NVal *b_min(int line, NVal *x);
NVal *b_sorted(int line, NVal *x);
NVal *b_reversed(int line, NVal *x);
NVal *b_unique(int line, NVal *x);
NVal *b_keys(int line, NVal *x);
NVal *b_starts_with(int line, NVal *x, NVal *p);
NVal *b_ends_with(int line, NVal *x, NVal *p);
NVal *b_count_of(int line, NVal *x, NVal *n);
NVal *b_niko_range(int line, NVal *a, NVal *b);
NVal *b_ok(int line, NVal *x);
NVal *b_error(int line, NVal *x);
NVal *b_is_ok(int line, NVal *x);
NVal *b_is_error(int line, NVal *x);
NVal *b_unwrap(int line, NVal *x);
NVal *b_unwrap_or(int line, NVal *x, NVal *d);
NVal *b_error_message(int line, NVal *x);
NVal *b_try_number(int line, NVal *x);
NVal *b_try_read_file(int line, NVal *x);
NVal *b_random_int(int line, NVal *a, NVal *b);
NVal *b_pick(int line, NVal *x);
NVal *b_today(int line);
NVal *b_now(int line);
NVal *b_sleep(int line, NVal *x);
NVal *b_write_file(int line, NVal *p, NVal *t);
NVal *b_append_file(int line, NVal *p, NVal *t);
NVal *b_read_file(int line, NVal *p);
NVal *b_read_lines(int line, NVal *p);
NVal *b_file_exists(int line, NVal *p);

#endif
