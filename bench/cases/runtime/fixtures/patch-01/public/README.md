# Name normalization

Current implementation trims surrounding whitespace and preserves case.
Requested change: normalize_name must also Unicode-casefold the trimmed text.
Preserve empty-text and TypeError contracts. Update implementation, tests and
this documentation together in one reviewed patch. Include executable JSON
examples for inputs " Alice " and " Straße " (with canonical outputs).
