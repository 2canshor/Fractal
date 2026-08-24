# Third-Party Notices

## Hermes Dojo session signal adaptation

Fractal's staged `fractal.donor_signals` module adapts bounded detection ideas from:

- Project: Hermes Dojo
- Source: <https://github.com/Yonkoo11/hermes-dojo>
- Commit: `ee114e72e18b13d3aeb4b76a8d1ade0916972248`
- Original files: `scripts/monitor.py`, `scripts/analyzer.py`

The adaptation retains tool-failure, possible-user-correction and retry-loop signal ideas. It removes Hermes storage coupling, Skill mutation, Skill creation, GEPA, cron, report delivery and promotion authority. The adapted output is privacy-bounded evidence routed to Fractal's `Find Problems` Step; it cannot approve or apply a change.

MIT License

Copyright (c) 2026 Yonkoo11

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
