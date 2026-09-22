examples/packages/ — Alpha 16 package-manager example.

1. Install the package (reads its niko.toml, copies into ~/.niko/packages/):

     niko2 get examples/packages/hello-pkg

2. Run the consumer program:

     niko2 run examples/packages/hello-app/main.niko

   It imports "pkg:hello-pkg/greet.niko" as greet — the version is picked
   from the local cache (newest cached, or the niko.lock pin if you run
   `niko2 lock` in the app's folder first).

3. Pin it:

     cd examples/packages/hello-app && niko2 lock .

Flags go AFTER the source: `niko2 get <src> --force` (reinstall).
No network is used unless you `get` from a git URL.
