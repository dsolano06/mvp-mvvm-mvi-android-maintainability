"""
probe_grammar.py — one-off helper to discover tree-sitter node-type names for the
Kotlin and Java grammars, so extract_metrics.py targets the real vocabulary instead
of guessing. Prints the named-node S-expression for small samples.

Usage: python analysis/probe_grammar.py
"""
from tree_sitter import Parser
from tree_sitter_language_pack import get_language

KOTLIN = r'''
package com.x
import a.b.Repo
class FooViewModel(private val repo: Repo) : ViewModel() {
    private val _state = MutableStateFlow(UiState())
    val state: StateFlow<UiState> = _state
    fun load(id: Int) {
        if (id > 0 && repo.ready()) {
            for (i in 0..id) { _state.value = repo.get(i) ?: UiState() }
        } else when (id) { 0 -> reset(); else -> {} }
    }
    private fun reset() { _state.value = UiState() }
}
'''

JAVA = r'''
package com.x;
import a.b.Repo;
public class FooPresenter implements Contract.View {
    private final Repo repo;
    private int count;
    public FooPresenter(Repo repo) { this.repo = repo; }
    public void load(int id) {
        if (id > 0 && repo.ready()) {
            for (int i = 0; i < id; i++) { count += repo.get(i); }
        } else { count = 0; }
    }
}
'''


def dump(lang_name, src):
    lang = get_language(lang_name)
    parser = Parser(lang)
    tree = parser.parse(src.encode())
    print(f"\n===== {lang_name} =====")

    def walk(node, depth=0):
        if node.is_named:
            txt = node.text.decode()[:30].replace("\n", " ")
            print("  " * depth + f"{node.type}: {txt!r}")
        for ch in node.children:
            walk(ch, depth + 1)

    walk(tree.root_node)


if __name__ == "__main__":
    dump("kotlin", KOTLIN)
    dump("java", JAVA)
