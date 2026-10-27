# 3. One toolbox builds the tool list

**Context.** Tools were registered by five functions, called in an order written out in
`server.create_app` and copied by hand into three tests and a bench, with one copy in a
different order. Tools fetched their dependencies from an untyped `Services.extra` dict and
handled a missing key in three ways. Production code bent to fit fakes (`except TypeError`,
`getattr(s, "readable")`).

**Decision.** `brain/toolbox/` holds the tools by family. `build(Services)` is the one
registration order, and the tool list the model sees follows it. `Services` is typed. A tool is
registered only when what it acts on exists, so no tool checks a dependency at run time.
`catalogue()` gives the full list for describing it (the voice prompt, the benches). Tests build
real registries through `build`; they never wire tools by hand.

**Cost.** A test that wants one tool provides its dependency, even if only a stand-in.
