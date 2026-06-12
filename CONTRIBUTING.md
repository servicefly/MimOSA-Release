# Contributing to MimOSA

Thanks for helping build MimOSA! This guide covers the git workflow, commit
conventions, and local checks we use to keep the project healthy.

---

## 🧭 Project conventions

- **Language:** Python 3.10+ (developed on 3.11).
- **Style:** PEP 8. Keep lines ≤ 88 chars where practical. Prefer clear names
  and docstrings over cleverness.
- **Docstrings:** Every module, public class, and public function gets a
  docstring. Explain *why*, not just *what* — especially around the LLM
  abstraction and any future local-LLM hooks.
- **Privacy:** Never log or transmit sensitive user data. All LLM access goes
  through `mimosa.llm` so privacy/local-only mode can be enforced centrally.

---

## 🌿 Branching model

We use short-lived feature branches off `main`.

| Branch type | Naming | Example |
|-------------|--------|---------|
| Milestone work | `milestone/<id>` | `milestone/m1.1` |
| Feature | `feature/<short-desc>` | `feature/whisper-stt` |
| Bug fix | `fix/<short-desc>` | `fix/wake-word-crash` |
| Docs only | `docs/<short-desc>` | `docs/architecture` |

```bash
git checkout main
git pull
git checkout -b feature/my-thing
```

Open a Pull Request into `main`. **Do not merge your own PR without review** —
maintainers verify each PR before merging.

---

## ✅ Commit message convention

We follow **[Conventional Commits](https://www.conventionalcommits.org/)**:

```
<type>(<scope>): <short summary>
```

**Types:** `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`, `build`.

**Examples:**

```
feat(m1.1): Complete project setup and LLM abstraction layer
feat(voice): add Porcupine wake-word detection
fix(llm): handle non-2xx responses from RouteLLM
docs(architecture): explain local provider hook
test(setup): validate provider factory env selection
```

Make **incremental commits** — one logical change per commit — rather than one
giant commit at the end.

---

## 🔧 Local checks before pushing

Run these from the repo root and make sure they pass:

```bash
# 1. Environment health
python scripts/health_check.py

# 2. Test suite
pytest
```

If you added a dependency, pin it in `requirements.txt` with a version
constraint and explain why in your PR.

---

## 🔌 Adding a new LLM provider

The LLM layer is designed for extension (see
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)). To add one:

1. Create `mimosa/llm/<name>_provider.py` with a class subclassing
   `BaseLLMProvider` (implement `chat`, `health_check`, and set `is_local`).
2. Register it in `PROVIDER_REGISTRY` in
   `mimosa/llm/provider_factory.py`.
3. Add tests to `tests/`.

No calling code should ever import a concrete provider directly — always go
through `create_provider()`.

---

## 📄 License

By contributing, you agree your contributions are licensed under the project's
MIT License.
