"""Internationalization and French localization for Smart Search."""

from __future__ import annotations

import locale
import os
import re
from typing import Any


def is_french_locale() -> bool:
    """Detect if Anki or the environment is currently using French."""
    # 0. Explicit environment override (e.g. for testing)
    override = os.environ.get("SMART_SEARCH_LANG", "").strip().lower()
    if override:
        return override.startswith("fr")

    # 1. Anki profile / meta language setting (authoritative inside Anki)
    try:
        from aqt import mw
        if mw is not None and hasattr(mw, "pm") and mw.pm is not None:
            if hasattr(mw.pm, "meta") and mw.pm.meta:
                for key in ("defaultLang", "lang", "language"):
                    val = mw.pm.meta.get(key)
                    if val is not None:
                        return str(val).strip().lower().startswith("fr")
            if hasattr(mw.pm, "profile") and mw.pm.profile:
                for key in ("defaultLang", "lang", "language"):
                    val = mw.pm.profile.get(key)
                    if val is not None:
                        return str(val).strip().lower().startswith("fr")
    except Exception:
        pass

    # 2. Anki translation module
    try:
        from aqt import tr as aqt_tr
        if hasattr(aqt_tr, "current_lang") and aqt_tr.current_lang:
            return str(aqt_tr.current_lang).strip().lower().startswith("fr")
    except Exception:
        pass

    # 3. Standard locale environment variables
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        val = os.environ.get(var, "").strip().lower()
        if val:
            return val.startswith("fr")

    return False


FRENCH_TRANSLATIONS: dict[str, str] = {
    # Modes
    "Smart": "Intelligent",
    "Exact": "Exact",
    "Semantic": "Sémantique",

    # Mode tooltips
    "Typo-tolerant search with medical aliases and instant local indexing":
        "Recherche tolérante aux fautes de frappe avec alias médicaux et indexation locale instantanée",
    "Standard Anki query syntax with native filters":
        "Syntaxe de recherche Anki standard avec filtres natifs",
    "Search by clinical meaning using local neural embeddings":
        "Recherche par sens clinique via embeddings neuronaux locaux",

    # Previews
    "Question": "Question",
    "Answer": "Réponse",
    "Edit": "Modifier",
    "Front": "Recto",
    "Back": "Verso",
    "Fields": "Champs",
    "Tags": "Tags",
    "Deck": "Paquet",

    # Badges
    "exact": "exact",
    "title": "titre",
    "tag": "tag",
    "typo fix": "correction",
    "alias": "alias",
    "semantic": "sémantique",

    # Deck picker / Scope
    "All decks": "Tous les paquets",
    "Filter by deck...": "Filtrer par paquet...",
    "Search decks...": "Rechercher un paquet...",
    "Clear filter": "Effacer le filtre",
    "Current deck": "Paquet actuel",
    "Custom decks": "Paquets personnalisés",

    # Search bar & placeholder
    "Search cards (Ctrl+K)...": "Rechercher des cartes (Ctrl+K)...",
    "Search cards (⌘K)...": "Rechercher des cartes (⌘K)...",
    "Search cards...": "Rechercher des cartes...",

    # Toolbar buttons
    "Select": "Sélectionner",
    "Select all": "Tout sélectionner",
    "Deselect all": "Tout désélectionner",
    "All shown": "Toutes affichées",
    "None": "Aucune",
    "Invert": "Inverser",
    "Invert selection": "Inverser la sélection",
    "Open in Browser": "Ouvrir dans le navigateur",
    "Browser": "Navigateur",
    "Related": "Cartes liées",
    "Find Related Cards": "Trouver les cartes liées",
    "Back to search": "Retour à la recherche",
    "‹ Back to search": "‹ Retour à la recherche",
    "Undo": "Annuler",
    "Settings": "Paramètres",
    "Refresh": "Actualiser",
    "Retry": "Réessayer",

    # Actions / Context Menu
    "Flag": "Drapeau",
    "Clear": "Effacer",
    "Clear Flag": "Effacer le drapeau",
    "Clear flag": "Effacer le drapeau",
    "Red": "Rouge",
    "Orange": "Orange",
    "Green": "Vert",
    "Blue": "Bleu",
    "Pink": "Rose",
    "Turquoise": "Turquoise",
    "Purple": "Violet",
    "Suspend": "Suspendre",
    "Unsuspend": "Réactiver",
    "Bury": "Enfouir",
    "Unbury": "Désenfouir",
    "Tags": "Tags",
    "Add…": "Ajouter…",
    "Remove…": "Retirer…",
    "Add Tag…": "Ajouter un tag…",
    "Remove Tag…": "Retirer un tag…",
    "Add tags": "Ajouter des tags",
    "Add tags...": "Ajouter des tags...",
    "Remove tags": "Retirer des tags",
    "Remove tags...": "Retirer des tags...",
    "Move cards": "Déplacer les cartes",
    "Move cards...": "Déplacer les cartes...",
    "Move to deck...": "Déplacer vers le paquet...",
    "Change Deck…": "Changer de paquet…",
    "Create copy": "Créer une copie",
    "Create copy...": "Créer une copie...",
    "Create Copy…": "Créer une copie…",
    "Close": "Fermer",
    "Replay": "Rejouer",
    "Card": "Carte",
    "Expand preview": "Agrandir l'aperçu",
    "Close preview": "Fermer l'aperçu",

    # Flag colors (lowercase)
    "red": "rouge",
    "orange": "orange",
    "green": "vert",
    "blue": "bleu",
    "pink": "rose",
    "turquoise": "turquoise",
    "purple": "violet",

    # Empty & Result states
    "No cards match this search.": "Aucune carte ne correspond à cette recherche.",
    "No results found": "Aucun résultat trouvé",
    "Try a different search or change search mode.": "Essayez une autre recherche ou changez de mode de recherche.",
    "Related to:": "Lié à :",
    "Related cards": "Cartes liées",
    "Across all decks": "Dans tous les paquets",
    "card": "carte",
    "cards": "cartes",
    "note": "note",
    "notes": "notes",
    "selected": "sélectionnée(s)",
    "found": "trouvée(s)",

    # Settings & About
    "Search Settings": "Paramètres de recherche",
    "Search settings": "Paramètres de recherche",
    "Search": "Recherche",
    "Default mode": "Mode par défaut",
    "Result limit": "Limite de résultats",
    "Card preview": "Aperçu de la carte",
    "Open previews as": "Ouvrir l'aperçu en",
    "Show automatically while browsing results": "Afficher automatiquement lors de la navigation dans les résultats",
    "Semantic search": "Recherche sémantique",
    "Preview": "Aperçu",
    "About": "À propos",
    "Keyboard shortcuts": "Raccourcis clavier",
    "Keyboard Shortcuts": "Raccourcis clavier",
    "Close Settings": "Fermer les paramètres",

    # Status Strings & Summaries
    "Smart & Exact ready": "Intelligent et Exact prêts",
    "Preparing Smart & Exact": "Préparation Intelligent et Exact",
    "Smart & Exact need setup": "Intelligent et Exact nécessitent une configuration",
    "Smart & Exact need attention": "Intelligent et Exact nécessitent votre attention",
    "Index ready": "Index prêt",
    "Indexing...": "Indexation en cours...",
    "Rebuilding index...": "Reconstruction de l'index...",
    "Ready": "Prêt",
    "Semantic search ready": "Recherche sémantique prête",
    "Preparing semantic search": "Préparation de la recherche sémantique",
    "Preparing semantic search...": "Préparation de la recherche sémantique...",
    "Semantic search unavailable on this computer": "Recherche sémantique non disponible sur cet ordinateur",
    "Semantic search needs setup": "La recherche sémantique nécessite une configuration",
    "Semantic search needs preparation": "La recherche sémantique nécessite une préparation",
    "Semantic search preparation starts automatically": "La préparation de la recherche sémantique démarre automatiquement",
    "Restart Anki to finish Semantic search": "Redémarrez Anki pour finaliser la recherche sémantique",
    "Semantic search needs attention": "La recherche sémantique nécessite votre attention",
    "Search data could not be opened. Try refreshing it.": "Impossible d'ouvrir l'index de recherche. Essayez d'actualiser.",
    "This profile needs initial search setup.": "Ce profil nécessite la préparation initiale de la recherche.",
    "Open an Anki profile to initialize Smart Search.": "Ouvrez un profil Anki pour initialiser Smart Search.",
    "Starting snapshot.": "Démarrage de l'instantané.",
    "Finishing setup.": "Finalisation de la configuration.",
    "Refresh cancelled.": "Actualisation annulée.",
    "No specific UWorld or AMBOSS source tags were found on the selected note.":
        "Aucun tag de source spécifique trouvé sur la note sélectionnée.",
    "No other cards share the selected UWorld or AMBOSS source tag.":
        "Aucune autre carte ne partage le tag sélectionné.",

    # Browser handoff & Search field
    "Open in Smart Search": "Ouvrir dans Smart Search",
    "Open this search in Smart Search": "Ouvrir cette recherche dans Smart Search",
    "Search in Anki Browser": "Rechercher dans le navigateur Anki",
    "Search notes, tags, decks…": "Rechercher des notes, tags, paquets…",
    "Literal": "Littéral",
}


def tr(text: str, **kwargs: Any) -> str:
    """Translate string if French locale is active, formatted with optional kwargs."""
    if not text:
        return text
    if is_french_locale():
        translated = FRENCH_TRANSLATIONS.get(text, text)
    else:
        translated = text
    if kwargs:
        try:
            return translated.format(**kwargs)
        except Exception:
            return translated
    return translated


def tr_plural(singular_en: str, plural_en: str, count: int) -> str:
    """Return translated singular or plural label based on count."""
    text = singular_en if count == 1 else plural_en
    return tr(text)


def get_help_html(primary_key: str = "Ctrl") -> str:
    """Return help HTML in French or English based on active locale."""
    if is_french_locale():
        return f"""\
<h3>Rechercher dans votre collection</h3>
<p>Tapez ci-dessus pour rechercher vos cartes. Le mode <b>Intelligent</b> corrige les fautes de frappe et développe les alias médicaux français et internationaux ; <b>Exact</b> utilise la syntaxe standard d'Anki ; <b>Sémantique</b> recherche par sens clinique.</p>
<p><b>Exact :</b> <code>insuffisance cardiaque</code> exige les deux termes distincts, alors que <code>&quot;insuffisance cardiaque&quot;</code> exige l'expression exacte. Utilisez <code>w:terme</code> pour un mot entier.</p>
<p><b>Intelligent et Exact</b> sont immédiatement prêts et rapides. Utilisez des filtres structurés tels que <b>deck:EDN</b>, <b>tag:cardio</b>, ou <b>notetype:Texte</b> pour affiner vos résultats.</p>
<ul>
<li><b>Bas / Haut</b> — naviguer dans les résultats ; l'aperçu s'affiche en direct</li>
<li><b>Espace / Flèche Droite</b> — afficher la réponse de la carte</li>
<li><b>Flèche Gauche</b> — revenir au recto de la carte</li>
<li><b>Entrée dans le champ de recherche</b> — lancer la recherche immédiatement</li>
<li><b>Entrée dans les résultats</b> — ouvrir la note en surbrillance dans le navigateur Anki</li>
<li><b>Ctrl+Shift+P</b> — afficher / masquer le volet d'aperçu</li>
<li><b>Shift+Espace</b> — cocher ou décocher le résultat en surbrillance pour actions groupées</li>
<li><b>Shift+Clic</b> — sélectionner une plage de résultats</li>
<li><b>Clic droit</b> — cartes liées, copier, déplacer, enfouir, drapeau, suspendre ou taguer</li>
<li><b>{primary_key}+Entrée</b> — ouvrir les résultats sélectionnés dans le navigateur</li>
<li><b>{primary_key}+1 / 2 / 3</b> — Modes Intelligent, Exact, Sémantique</li>
<li><b>{primary_key}+L</b> — revenir au champ de recherche</li>
<li><b>Échap</b> — effacer la requête, puis fermer la fenêtre</li>
</ul>
"""
    return f"""\
<h3>Search your collection</h3>
<p>Type above to search notes. Smart mode fixes typos and expands common
medical aliases; Exact uses Anki's standard search; Semantic matches by meaning.</p>
<p><b>Exact:</b> <code>heart failure</code> requires both separate terms,
while <code>&quot;heart failure&quot;</code> requires that phrase. Use
<code>w:heart</code> when you need a whole-word match.</p>
<p><b>Smart and Exact</b> use the initial fast-search setup. <b>Semantic</b>
has a separate one-time preparation that takes longer. You can keep using
Smart and Exact while Semantic is being prepared.</p>
<p>Use structured filters such as <b>deck:AnKing</b>, <b>tag:cardio</b>, or
<b>notetype:Cloze</b> to narrow results.</p>
<ul>
<li><b>Down / Up</b> — move through results; the preview follows</li>
<li><b>Space / Right Arrow</b> — show the card answer</li>
<li><b>Left Arrow</b> — return to the card front</li>
<li><b>Return in the search field</b> — run the current search immediately</li>
<li><b>Return in the results</b> — open the highlighted note in the Browser</li>
<li><b>Ctrl+Shift+P</b> — toggle the card preview pane</li>
<li><b>Shift+Space</b> — check or uncheck the highlighted result for bulk actions</li>
<li><b>Shift+click</b> — check a range of results</li>
<li><b>Right-click</b> — find related cards, create a copy, move, bury, flag, suspend, or tag results</li>
<li><b>{primary_key}+Return</b> — open checked results, or all shown if none are checked</li>
<li><b>{primary_key}+1 / 2 / 3</b> — Smart, Exact, Semantic mode</li>
<li><b>{primary_key}+L</b> — jump back to the search field</li>
<li><b>Esc</b> — clear the query, then close</li>
</ul>
"""
