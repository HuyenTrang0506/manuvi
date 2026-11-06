# SP-02 report (metrics only)
Page 666 (printed 43-6), 102 spans. Merge = same-line neighbours with gap <= 0.6 x font size, plus lower-case continuation lines.
The 36 baseline from the spec could not be reproduced; counts depend on the definition of suspicious.
- Strict definition (single letters, short lower-case single tokens, hyphen edges, short mixed-case remnants): 4 -> 0 (83 labels after merge).
- Loose definition (any lower-case start): 29 -> 10; the 10 left are whole lower-case phrases, not fragments.
- Neighbours, strict, before -> after (gap 0.6): p663 4->1, p664 18->15, p667 3->1, p668 0->0.
- Remaining on p664: single capital letters (connector designators), legitimate labels not fragments.
- Gap 1.0 x size merges more (p666 102 -> 75) with more risk of joining separate labels; 0.6 recommended.
