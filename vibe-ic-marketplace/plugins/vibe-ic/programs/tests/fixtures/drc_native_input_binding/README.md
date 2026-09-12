# Native zero DRC fixture

The complete, unpadded KLayout XML and terminal transcript were captured from
the open gf180mcuD signoff deck on the isolated quarter-phase fill candidate.
Native exit status was zero; the XML contains zero violation items. These are
test inputs, not certification of any test-generated GDS or current design.

Captured source: `8b6c977e455233769d5c9d86d690dcb964f01958`.
Image digest: `sha256:89a8fd7295208ee6d06e216ade9edc6161d26db52099e9f22ceb77a2d76e3f49`.
Input GDS SHA-256: `2fe8c19c276368af07359e214a700494c631789b78d53883537b899b5964947f`.
XML SHA-256: `ddf9e1b48cace41f7ffabbd2949d8e85e932faf447f2652260116e765b88bf3b`.
Log SHA-256: `92acff4caaee3151a1369c785dd7de6e27d754e751c57a34b1d17274bc5ac3c6`.

The negative test explicitly inserts one XML item and changes its terminal
count. The unmodified pair is used for the positive and stale-layout controls.
The smaller no-corroboration fixture remains a negative input in its existing
test module; the report-size and authenticity rules are unchanged.
The XML is 139888 bytes and the transcript is 102694 bytes; neither is padded.
Paths name only public tool installations and the isolated `/evidence` mount.
