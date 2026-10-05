"""Shell-completion scripts, derived from the argparse builders.

``comic_dl.cli`` owns the parser builders; this module only reads them
(lazily, to avoid an import cycle) and renders per-shell scripts.
"""

from __future__ import annotations

import argparse


def _parser_flags(parser: argparse.ArgumentParser) -> list[str]:
    """Option strings of a parser, for completion candidates."""
    flags: list[str] = []
    for action in parser._actions:
        flags.extend(action.option_strings)
    return sorted(set(flags))


def _parser_subcommands(parser: argparse.ArgumentParser) -> list[str]:
    """Subcommand names of a parser, for completion candidates."""
    names: list[str] = []
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            names.extend(action.choices)
    return sorted(names)


def _subcommand_parser(
    parser: argparse.ArgumentParser, name: str
) -> argparse.ArgumentParser | None:
    """The child parser for subcommand ``name``, or ``None``."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            choice = action.choices.get(name)
            if isinstance(choice, argparse.ArgumentParser):
                return choice
    return None


def _parser_flag_help(parser: argparse.ArgumentParser) -> dict[str, str]:
    """Map each option string to its help text (tooltips for rich shells)."""
    helps: dict[str, str] = {}
    for action in parser._actions:
        for opt in action.option_strings:
            helps[opt] = action.help or ""
    return helps


def _merge_help(*lists: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """Merge (word, help) lists, first help winning on duplicates."""
    seen: dict[str, str] = {}
    for pairs in lists:
        for word, tip in pairs:
            seen.setdefault(word, tip)
    return sorted(seen.items())


def _subtree_pairs(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Subcommand names plus every descendant flag, paired with help text."""
    pairs = [(c, "") for c in _parser_subcommands(parser)]
    kids = [_subcommand_parser(parser, name) for name in _parser_subcommands(parser)]
    return pairs + _merge_help(*[_words_with_help(k) for k in kids if k])


def _zsh_compadd(pairs: list[tuple[str, str]]) -> str:
    """A ``compadd -d`` block: words with a parallel description array."""
    words = " ".join(word for word, _ in pairs)
    quoted = " ".join("'" + tip.replace("'", "'\\''") + "'" for _, tip in pairs)
    return f"local -a _d; _d=({quoted}); compadd -d _d -- {words}"


def _fish_word_lines(condition: str, pairs: list[tuple[str, str]]) -> str:
    """One ``complete`` line per word, each carrying its description."""
    return "\n".join(
        f'complete -c comic-dl -n "{condition}" -a "{w}\t{t}"'.replace("'", "'\\''")
        for w, t in pairs
    )


def _subtree_words(parser: argparse.ArgumentParser) -> str:
    """Subcommand names plus every descendant flag, for completion candidates."""
    kids = [_subcommand_parser(parser, name) for name in _parser_subcommands(parser)]
    words = _parser_subcommands(parser)
    words += sorted({w for kid in kids if kid for w in _parser_flags(kid)})
    return " ".join(dict.fromkeys(words))


def _completion_global_flags() -> list[str]:
    """Option strings of the first-stage parser, for completion candidates."""
    from . import _build_first_stage_parser

    return _parser_flags(_build_first_stage_parser())


def _completion_commands() -> list[str]:
    """Top-level command names, for completion candidates."""
    from . import _LIBRARY_COMMANDS

    return sorted(
        set(_LIBRARY_COMMANDS)
        | {
            "update",
            "self",
            "list-sources",
            "cookie",
            "cache",
            "config",
            "plugin",
            "completion",
            "help",
        }
    )


def _completion_script(shell: str) -> str:
    """Static completion script for ``shell`` (bash/zsh/fish), derived from
    the argparse definitions."""
    from . import (
        _LIBRARY_COMMANDS,
        _build_cache_parser,
        _build_config_parser,
        _build_cookie_parser,
        _build_first_stage_parser,
        _build_list_sources_parser,
        _build_self_parser,
        _build_self_site_parser,
        _build_update_parser,
    )
    from .library import _build_parser as _build_library_parser

    flags = " ".join(_completion_global_flags())
    commands = " ".join(_completion_commands())
    update_flags = " ".join(_parser_flags(_build_update_parser()))

    self_parser = _build_self_parser()
    self_flags = " ".join(_parser_subcommands(self_parser))
    self_update = _subcommand_parser(self_parser, "update")
    self_update_flags = " ".join(_parser_flags(self_update)) if self_update else ""

    site_parser = _build_self_site_parser()
    self_site_flags = " ".join(_parser_subcommands(site_parser))
    site_kids = [_subcommand_parser(site_parser, name) for name in _parser_subcommands(site_parser)]
    self_site_sub_flags = " ".join(
        sorted({w for kid in site_kids if kid for w in _parser_flags(kid)})
    )

    cookie_parser = _build_cookie_parser()
    cookie_flags = _subtree_words(cookie_parser)

    cache_parser = _build_cache_parser()
    cache_flags = _subtree_words(cache_parser)

    config_parser = _build_config_parser()
    config_flags = _subtree_words(config_parser)

    sources_flags = " ".join(_parser_flags(_build_list_sources_parser()))

    lib_flag_words: set[str] = set()
    for cmd in _LIBRARY_COMMANDS:
        lib_flag_words.update(_parser_flags(_build_library_parser(cmd)))
    lib_flags = " ".join(sorted(lib_flag_words))

    # (word, help) pairs for shells with a description channel.
    first_parser = _build_first_stage_parser()
    top_pairs = [(c, "") for c in _completion_commands()] + _words_with_help(first_parser)
    update_pairs = _words_with_help(_build_update_parser())
    self_update_pairs = _words_with_help(self_update) if self_update else []
    site_sub_pairs = _merge_help(*[_words_with_help(k) for k in site_kids if k])
    cookie_pairs = _subtree_pairs(cookie_parser)
    cache_pairs = _subtree_pairs(cache_parser)
    config_pairs = _subtree_pairs(config_parser)
    sources_pairs = _words_with_help(_build_list_sources_parser())
    lib_help: dict[str, str] = {}
    for cmd in _LIBRARY_COMMANDS:
        for word, tip in _words_with_help(_build_library_parser(cmd)):
            lib_help.setdefault(word, tip)
    lib_pairs = sorted(lib_help.items())

    if shell == "bash":
        return f"""# bash completion for comic-dl
# Add to your shell:  source <(comic-dl completion bash)
_comic_dl_complete() {{
    local cur="${{COMP_WORDS[COMP_CWORD]}}"
    if [[ "${{COMP_CWORD}}" -eq 1 ]]; then
        COMPREPLY=($(compgen -W "{commands} {flags}" -- "${{cur}}"))
        return
    fi
    if [[ "${{COMP_WORDS[1]}}" == "self" && "${{COMP_CWORD}}" -ge 3 ]]; then
        case "${{COMP_WORDS[2]}}" in
            update) COMPREPLY=($(compgen -W "{self_update_flags}" -- "${{cur}}")); return ;;
            site)
                if [[ "${{COMP_CWORD}}" -ge 4 ]]; then
                    COMPREPLY=($(compgen -W "{self_site_sub_flags}" -- "${{cur}}"))
                else
                    COMPREPLY=($(compgen -W "{self_site_flags}" -- "${{cur}}"))
                fi
                return ;;
            *) COMPREPLY=($(compgen -W "{self_flags}" -- "${{cur}}")); return ;;
        esac
    fi
    case "${{COMP_WORDS[1]}}" in
        update) COMPREPLY=($(compgen -W "{update_flags}" -- "${{cur}}")); return ;;
        self)   COMPREPLY=($(compgen -W "{self_flags}" -- "${{cur}}")); return ;;
        cookie) COMPREPLY=($(compgen -W "{cookie_flags}" -- "${{cur}}")); return ;;
        cache)  COMPREPLY=($(compgen -W "{cache_flags}" -- "${{cur}}")); return ;;
        config) COMPREPLY=($(compgen -W "{config_flags}" -- "${{cur}}")); return ;;
        plugin) COMPREPLY=($(compgen -W "list validate scaffold" -- "${{cur}}")); return ;;
        list-sources) COMPREPLY=($(compgen -W "{sources_flags}" -- "${{cur}}")); return ;;
        help)   COMPREPLY=($(compgen -W "{commands}" -- "${{cur}}")); return ;;
    esac
    COMPREPLY=($(compgen -W "{lib_flags} {flags}" -- "${{cur}}"))
}}
complete -o default -F _comic_dl_complete comic-dl
"""
    if shell == "zsh":
        fallback_pairs = lib_pairs + _words_with_help(first_parser)
        return f"""#compdef comic-dl
# Add to your shell:  eval "$(comic-dl completion zsh)"
_comic_dl() {{
    if (( CURRENT == 2 )); then
        {_zsh_compadd(top_pairs)}
        return
    fi
    if [[ "${{words[2]}}" == "self" && CURRENT -ge 3 ]]; then
        case "${{words[3]}}" in
            update) {_zsh_compadd(self_update_pairs)} ;;
            site)
                if (( CURRENT >= 5 )); then
                    {_zsh_compadd(site_sub_pairs)}
                else
                    compadd -- {self_site_flags}
                fi ;;
            *) compadd -- {self_flags} ;;
        esac
        return
    fi
    case "${{words[2]}}" in
        update) {_zsh_compadd(update_pairs)} ;;
        self)   compadd -- {self_flags} ;;
        cookie) {_zsh_compadd(cookie_pairs)} ;;
        cache)  {_zsh_compadd(cache_pairs)} ;;
        config) {_zsh_compadd(config_pairs)} ;;
        plugin) compadd -- list validate scaffold ;;
        list-sources) {_zsh_compadd(sources_pairs)} ;;
        help)   compadd -- {commands} ;;
        *)      {_zsh_compadd(fallback_pairs)} ;;
    esac
}}
compdef _comic_dl comic-dl
"""
    if shell == "fish":
        seen = "__fish_seen_subcommand_from"
        lines = [
            "complete -c comic-dl -f",
            _fish_word_lines("__fish_use_subcommand", top_pairs),
            _fish_word_lines(f"{seen} update", update_pairs),
            f'complete -c comic-dl -n "{seen} self" -a "{self_flags}"',
            _fish_word_lines(f"{seen} self; and {seen} update", self_update_pairs),
            f'complete -c comic-dl -n "{seen} self; and {seen} site" -a "{self_site_flags}"',
        ]
        for sub in self_site_flags.split():
            lines.append(
                _fish_word_lines(f"{seen} self; and {seen} site; and {seen} {sub}", site_sub_pairs)
            )
        lines += [
            _fish_word_lines(f"{seen} cookie", cookie_pairs),
            _fish_word_lines(f"{seen} cache", cache_pairs),
            _fish_word_lines(f"{seen} config", config_pairs),
            f'complete -c comic-dl -n "{seen} plugin" -a "list validate scaffold"',
            _fish_word_lines(f"{seen} list-sources", sources_pairs),
            f'complete -c comic-dl -n "{seen} help" -a "{commands}"',
            _fish_word_lines("not __fish_use_subcommand", lib_pairs),
        ]
        return (
            "# fish completion for comic-dl\n"
            "# Add to your shell:  comic-dl completion fish | source\n" + "\n".join(lines) + "\n"
        )
    if shell == "powershell":
        return _powershell_script()
    raise ValueError(f"unsupported shell: {shell!r} (expected bash, zsh, fish, or powershell)")


def _powershell_entries() -> list[tuple[str, list[tuple[str, str]]]]:
    """(``;``-joined command path, [(word, help)]) for every position."""
    from . import (
        _LIBRARY_COMMANDS,
        _build_cache_parser,
        _build_config_parser,
        _build_cookie_parser,
        _build_first_stage_parser,
        _build_list_sources_parser,
        _build_self_parser,
        _build_self_site_parser,
        _build_update_parser,
    )
    from .library import _build_parser as _build_library_parser

    first = _build_first_stage_parser()
    first_help = _parser_flag_help(first)
    top: list[tuple[str, str]] = [(c, "") for c in _completion_commands()]
    top += [(w, first_help.get(w, "")) for w in _parser_flags(first)]

    self_parser = _build_self_parser()
    update_parser = _build_update_parser()
    site_parser = _build_self_site_parser()

    entries: list[tuple[str, list[tuple[str, str]]]] = [
        ("comic-dl", top),
        ("comic-dl;update", _words_with_help(update_parser)),
        ("comic-dl;self", [(c, "") for c in _parser_subcommands(self_parser)]),
    ]
    self_update = _subcommand_parser(self_parser, "update")
    if self_update is not None:
        entries.append(("comic-dl;self;update", _words_with_help(self_update)))
    entries.append(("comic-dl;self;site", [(c, "") for c in _parser_subcommands(site_parser)]))
    for name in _parser_subcommands(site_parser):
        child = _subcommand_parser(site_parser, name)
        if child is not None:
            entries.append((f"comic-dl;self;site;{name}", _words_with_help(child)))
    for label, builder in (
        ("comic-dl;cookie", _build_cookie_parser),
        ("comic-dl;cache", _build_cache_parser),
        ("comic-dl;config", _build_config_parser),
    ):
        entries.append((label, _subtree_entries(builder())))
    entries.append(("comic-dl;list-sources", _words_with_help(_build_list_sources_parser())))
    entries.append(("comic-dl;help", [(c, "") for c in _completion_commands()]))
    for cmd in sorted(_LIBRARY_COMMANDS):
        entries.append((f"comic-dl;{cmd}", _words_with_help(_build_library_parser(cmd))))
    entries.append(("comic-dl;plugin", [(c, "") for c in ("list", "validate", "scaffold")]))
    return entries


def _words_with_help(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Flag words of a parser paired with their help text."""
    helps = _parser_flag_help(parser)
    return [(w, helps.get(w, "")) for w in _parser_flags(parser)]


def _subtree_entries(parser: argparse.ArgumentParser) -> list[tuple[str, str]]:
    """Subcommand names plus every descendant flag, paired with help text."""
    words = [(c, "") for c in _parser_subcommands(parser)]
    seen = {c for c, _ in words}
    for name in _parser_subcommands(parser):
        child = _subcommand_parser(parser, name)
        if child is None:
            continue
        for word, tip in _words_with_help(child):
            if word not in seen:
                seen.add(word)
                words.append((word, tip))
    return words


def _powershell_script() -> str:
    """Static PowerShell completion script, derived from the argparse definitions.

    Shape mirrors the reference implementation (uv/clap): a ``switch`` over
    the ``;``-joined command path returning ``CompletionResult`` entries with
    tooltips, filtered by the word being completed.
    """

    def _quote(text: str) -> str:
        return text.replace("'", "''").replace("\n", " ")

    blocks = []
    for path, words in _powershell_entries():
        lines = [
            f"            [CompletionResult]::new('{w}', '{w}', "
            f"[CompletionResultType]::ParameterName, '{_quote(tip)}')"
            for w, tip in words
        ]
        blocks.append(
            f"        '{path}' {{\n" + "\n".join(lines) + "\n            break\n        }"
        )
    cases = "\n".join(blocks)
    return f"""using namespace System.Management.Automation
using namespace System.Management.Automation.Language

# PowerShell completion for comic-dl
# Save this output to a file and dot-source it from your $PROFILE.
Register-ArgumentCompleter -Native -CommandName comic-dl -ScriptBlock {{
    param($wordToComplete, $commandAst, $cursorPosition)

    $commandElements = $commandAst.CommandElements
    $command = @(
        'comic-dl'
        for ($i = 1; $i -lt $commandElements.Count; $i++) {{
            $element = $commandElements[$i]
            if ($element -isnot [StringConstantExpressionAst] -or
                $element.StringConstantType -ne [StringConstantType]::BareWord -or
                $element.Value.StartsWith('-') -or
                $element.Value -eq $wordToComplete) {{
                break
            }}
            $element.Value
        }}) -join ';'

    $completions = @(switch ($command) {{
{cases}
    }})

    $completions.Where{{ $_.CompletionText -like "$wordToComplete*" }} |
        Sort-Object -Property ListItemText
}}
"""
