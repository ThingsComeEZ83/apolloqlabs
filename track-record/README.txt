ApolloQLabs Live Track Record - public files
ledger.jsonl       every pick, daily mark and settlement, in order, each sealed with a SHA-256 fingerprint chain
ledger.csv         one row per trade (a spreadsheet-friendly summary of ledger.jsonl)
scorecard.json     the numbers shown on the site, recomputed from ledger.jsonl
picks/<date>.json  the exact ledger lines logged that day, committed to the public GitHub repo the same evening,
                   before anyone knows how the trades turn out (see the repo's commit history for the timestamp)
verify_ledger.py   run "python3 verify_ledger.py ledger.jsonl" to check the chain yourself
Paper trading for education. Not real money, not investment advice, not a recommendation.
