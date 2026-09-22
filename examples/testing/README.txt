Niko 2 -- Alpha 27 worked example: `niko2 test`
================================================

`math.niko` is the code under test; `math_test.niko` holds the tests.
A test is a top-level `to test_<name>:` function with no parameters.
The test file is an ordinary Niko program, so `import`/`use` of the
code under test work exactly like `niko2 run`.

Run it from this directory:

    python3 -m niko2 test

(or `niko2 test` once niko2 is on your PATH). Expected output:

    PASS test_add (math_test.niko)
    PASS test_mul (math_test.niko)
    PASS test_add_zero (math_test.niko)
    3 passed, 0 failed

Try breaking it -- change `give back a + b` in math.niko to
`give back a - b` -- and you will see the failure format:

    FAIL test_add (math_test.niko)
      Niko error in math_test.niko: add(20, 22) should be 42
    PASS test_mul (math_test.niko)
    FAIL test_add_zero (math_test.niko)
      Niko error in math_test.niko: adding zero should not change the number
    1 passed, 2 failed

Notes
-----
* Discovery: `niko2 test [dir]` (default: current directory) finds
  `*_test.niko` and `test_*.niko` recursively; hidden directories are
  skipped. You can also pass a single test file: `niko2 test math_test.niko`.
* Isolation: every test compiles and runs in a fresh VM, so globals set
  in one test are invisible in the next.
* A test fails when it raises a Niko error. The example uses
  `unwrap(error("message"))` to fail with a message; Alpha 27 (part 1)
  adds an `assert` statement as shorthand for the same thing, e.g.
  `assert math.add(20, 22) == 42` and
  `assert math.add(20, 22) == 42, "add is broken"`.
* `say` output inside tests prints straight through.
* Exit code is 0 iff every test passed. With no test files found the
  runner prints a message and exits 0.
* The runner uses the VM backend only (it is a dev tool; WASM/native
  are compile targets).
