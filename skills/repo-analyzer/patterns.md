# Anti-Pattern Detection Rules

These rules are used by the repo-analyzer to flag common code issues.

## Python

- **God class**: Class with >20 methods or >500 lines → High severity
- **Star imports**: `from module import *` → Medium severity
- **Bare except**: `except:` without specific exception → Medium severity
- **Mutable default args**: `def func(x=[])` → High severity
- **Hardcoded secrets**: Strings matching API key patterns → Critical severity

## JavaScript / TypeScript

- **Console.log in production**: `console.log` outside test files → Low severity
- **Any type abuse**: More than 10 `any` type annotations → Medium severity
- **Callback hell**: Nesting depth >4 in async code → Medium severity
- **Missing error handling**: `fetch()` without `.catch()` → High severity

## General

- **Large files**: >500 lines → Medium severity
- **Deep nesting**: >5 levels of indentation → Medium severity
- **TODO/FIXME count**: >20 across codebase → Low severity (but worth noting)
- **No tests**: No test files detected → High severity
- **No CI config**: No .github/workflows or similar → Medium severity
