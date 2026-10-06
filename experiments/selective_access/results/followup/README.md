# Fresh-process polygon memory

Complete unchanged HydroBASINS, HFX 0.3.0. Code commit
`946e989b7cd98db8ebf0d11180ceaf113a8aecfc`. [Saved measurements and exact commands](polygon-memory.json).
Eight sequential fresh processes; rows 5–8 reverse rows 1–4. No other coordinated
timed experiment ran concurrently. Other laptop activity and local OS cache are
uncontrolled. Each method loads the same attributes/locator and retains three
decoded outputs through READY. Measurements are laptop diagnostics.

Seconds; peak RSS in decimal MB (saved bytes / 1,000,000), rounded here only.
Preparation peak and READY peak are **process-lifetime** peaks at those boundaries,
not phase-only peaks. READY precedes witness serialization/equality checks.

| Order | Selection | Method | Attribute load s | Locator build s | Query s | Preparation peak MB | READY peak MB |
|---:|---|---|---:|---:|---:|---:|---:|
| 1 | ordinary | filtered | 0.0775 | 0.1479 | 0.3191 | 391.35 | 3543.70 |
| 2 | ordinary | explicit_groups | 0.0745 | 0.1488 | 0.0103 | 392.79 | 404.73 |
| 3 | largest | filtered | 0.0733 | 0.1520 | 0.2613 | 391.09 | 3264.45 |
| 4 | largest | explicit_groups | 0.0771 | 0.1559 | 0.2545 | 391.92 | 1502.61 |
| 5 | largest | explicit_groups | 0.0763 | 0.1487 | 0.1890 | 393.35 | 1504.36 |
| 6 | largest | filtered | 0.0743 | 0.1555 | 0.2618 | 392.00 | 3306.08 |
| 7 | ordinary | explicit_groups | 0.0759 | 0.1528 | 0.0104 | 391.89 | 403.05 |
| 8 | ordinary | filtered | 0.0773 | 0.1467 | 0.2919 | 390.74 | 3593.81 |

The ordinary IDs are `3110936770, 3110689390, 4110131420` (existing group 580).
The largest-group IDs are `6010000010, 6050010520, 2050067280` (existing group 0).
Both repeats return exactly three rows and retain/decode three geometries per query.
Separate-process comparisons passed exact ID, source WKB, and decoded-output WKB
equality across all four witnesses per selection. Witnesses stay outside Git.

| Selection | Method | Candidate groups / rows | Observable prefilter WKB bytes | Selected WKB bytes | Selected logical / backing bytes |
|---|---|---:|---:|---:|---:|
| Ordinary | filtered | 209 / 856422 | not measured | 4253 | 4291 / 5127 |
| Ordinary | explicit_groups | 1 / 4098 | 5227386 | 4253 | 4290 / 4294 |
| Largest | filtered | 94 / 385188 | not measured | 2459871 | 2459909 / 2460285 |
| Largest | explicit_groups | 1 / 4098 | 245517161 | 2459871 | 2459908 / 2459912 |

These buffer/WKB values are identical across repeats. Each process retains
53,953,750 attribute bytes excluding IDs, 30,289,824 ID-column bytes, and 60,587,040
locator NumPy bytes. Attribute logical and backing sizes match. Explicit candidate
logical/backing bytes are 5,276,562 / 5,276,566 (ordinary) and
245,566,337 / 245,566,341 (largest); candidate tables are no longer live at READY.
Selected table and all three decoded outputs remain live. Native decoded geometry
allocation bytes and filtered native intermediate WKB are **not measured**.

The JSON also contains startup, preparation, read/filter, decode, total query and
total-to-READY seconds, endpoint RSS, Arrow counters, exact reader settings,
object identity and receipt SHA256, versions, and framed witness digests.
Object/chunk sizes are storage metadata, not wire traffic. Physical I/O, wire
requests and wire response bytes are **not measured**. Initial acquisition,
interpreter imports, snap, graph, repair, union, and area work are excluded.

Run the saved `runs[].command` entries from the repository checkout in sequence;
each invokes a fresh process. Run `comparisons[].command` after all workers finish.
Change only local input/output paths as needed. The full run and snapshot contract
is in [the experiment README](../../README.md#fresh-process-polygon-memory-follow-up).
