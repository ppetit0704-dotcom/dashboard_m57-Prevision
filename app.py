"""
@author : Philippe PETIT
@version : 6.1.0
@description : Tableau de bord comptable M57
V6.1.0 : Projection N+1 compte par compte avec expander par chapitre,
recalcul dynamique des chapitres, card écart budgétaire,
graphique de tendance N-4 → N+1 global et par chapitre.
Corrections : add_shape pour lignes verticales, keys uniques plotly_chart
"""

import sys
from pathlib import Path
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

# =====================================================
# CONFIG STREAMLIT
# =====================================================

st.set_page_config(
    layout="wide",
    page_title="Tableau de bord comptable M57 (v6.1.0)",
    page_icon="📊",
    initial_sidebar_state="expanded"
)

# =====================================================
# PATH PROJET
# =====================================================

ROOT_DIR = Path(__file__).resolve().parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

# =====================================================
# IMPORTS INTERNES
# =====================================================

from core.loader import load_csv
from core.calculs import calculer_sommes_par_chapitre, calcul_autofinancement
from ui.sidebar import filtres
from ui.cards import afficher_indicateurs, badge, badgeRed, badgeGreen, badgeBlue
from ui.tables import tableau_chapitres
from ui.graphs import camembert

# =====================================================
# CONSTANTES
# =====================================================

COLUMN_ALIASES = {
    "Libelle_budget": ["Libellé_budget", "Libelle_budget"],
    "Realise":        ["Réalisé",        "Realise"],
    "Total_Prevu":    ["Total_Prévu",    "Total_Prevu"],
    "Reste_engage":   ["Reste_engagé",   "Reste_engage"],
}

HISTORIQUE_COLS = [
    "Liquidé_N_4",
    "Liquidé_N_3",
    "Liquidé_N_2",
    "Liquidé_N_1",
]

COULEURS = [
    "#4E79A7", "#F28E2B", "#E15759", "#76B7B2", "#59A14F",
    "#EDC948", "#B07AA1", "#FF9DA7", "#9C755F", "#BAB0AC",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
]


# =====================================================
# UTILITAIRES
# =====================================================

def normaliser_colonnes(df):
    df.columns = [c.strip() for c in df.columns]
    return df


def harmoniser_colonnes(df):
    for target, variants in COLUMN_ALIASES.items():
        for v in variants:
            if v in df.columns and target not in df.columns:
                df[target] = df[v]
    return df


def safe_sum(df, col):
    return df[col].sum() if col in df.columns else 0


def safe_div(a, b):
    return a / b if b else 0


# =====================================================
# HEADER
# =====================================================

def afficher_header():
    logo_path = ROOT_DIR / "assets" / "logo.png"
    if logo_path.exists():
        st.image(str(logo_path), width=420)

    st.title("📊 Tableau de bord comptable – M57")
    st.caption(
        "Version 6.1.0 | Auteur : Philippe PETIT | "
        "Projection N+1 compte par compte | Tendance N-4 → N+1"
    )


# =====================================================
# ANALYSE BUDGETAIRE
# =====================================================

def analyse_budget(total_budget, total_realise, reste_engage, ratio):
    st.subheader("🧠 Assistant budgétaire")

    taux_execution = safe_div(total_realise * 100, total_budget)

    if taux_execution >= 100:
        st.error("🔴 Dépassement budgétaire détecté")
    elif taux_execution >= 85:
        st.warning("🟠 Tension budgétaire")
    else:
        st.success("🟢 Situation budgétaire maîtrisée")

    c1, c2, c3 = st.columns(3)
    with c1:
        badge("Taux exécution (%)", round(taux_execution, 2))
    with c2:
        badge("Reste engagé", reste_engage)
    with c3:
        badge("Impact / habitant", round(ratio, 2))


# =====================================================
# CARD ÉCART BUDGÉTAIRE
# =====================================================

def afficher_ecart_budgetaire(total_n, total_n1):
    st.divider()
    st.subheader("📉 Écart budgétaire N → N+1")

    ecart_valeur  = round(total_n1 - total_n, 2)
    evolution     = round(((total_n1 / total_n) - 1) * 100, 2) if total_n else 0
    ecart_abs_pct = abs(evolution)

    if ecart_valeur < 0:
        st.success(
            f"🟢 Économie budgétaire : diminution de "
            f"{abs(ecart_valeur):,.2f} € ({evolution:+.2f} %)"
        )
    elif ecart_abs_pct >= 5:
        st.error(
            f"🔴 Tension forte : dépassement de "
            f"{ecart_valeur:,.2f} € ({evolution:+.2f} %)"
        )
    elif ecart_abs_pct >= 2:
        st.warning(
            f"🟠 Tension modérée : hausse de "
            f"{ecart_valeur:,.2f} € ({evolution:+.2f} %)"
        )
    else:
        st.info(
            f"🔵 Évolution maîtrisée : écart de "
            f"{ecart_valeur:,.2f} € ({evolution:+.2f} %)"
        )

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        badgeBlue("Total N", round(total_n, 2))
    with c2:
        badgeGreen("Total N+1", round(total_n1, 2))
    with c3:
        if ecart_valeur >= 0:
            badgeRed("Écart (€)", ecart_valeur)
        else:
            badgeGreen("Écart (€)", ecart_valeur)
    with c4:
        badge("Évolution (%)", evolution)


# =====================================================
# GRAPHIQUES TENDANCE
# =====================================================

def construire_series_tendance(df, chapitres_data):
    """
    Construit les séries temporelles N-4 → N+1 par chapitre.
    Retourne un dict {chapitre: {"labels": [...], "valeurs": [...]}}
    """
    cols_histo_presentes = [c for c in HISTORIQUE_COLS if c in df.columns]

    # Labels axe X dynamiques
    labels_x = []
    for col in cols_histo_presentes:
        rang = int(col.split("_N_")[1])
        labels_x.append(f"N-{rang}")
    labels_x.append("N")
    labels_x.append("N+1")

    series = {}
    for chapitre, edited_df in chapitres_data.items():
        valeurs = []

        # Historique N-4 → N-1
        for col in cols_histo_presentes:
            mask = df["Chapitre"] == chapitre
            val  = df.loc[mask, col].sum() if mask.any() else 0
            valeurs.append(round(val, 2))

        # Année N
        valeurs.append(round(edited_df["Année N"].sum(), 2))

        # Année N+1
        valeurs.append(round(edited_df["Année N+1"].sum(), 2))

        series[chapitre] = {"labels": labels_x, "valeurs": valeurs}

    return series


def graphique_tendance_global(series, titre="Tendance budgétaire N-4 → N+1"):
    """Graphique Plotly — toutes les courbes chapitres."""
    fig = go.Figure()

    premiere_serie = next(iter(series.values()))
    labels = premiere_serie["labels"]

    for i, (chapitre, data) in enumerate(series.items()):
        couleur = COULEURS[i % len(COULEURS)]

        # Courbe historique + N (trait plein)
        fig.add_trace(go.Scatter(
            x=labels[:-1],
            y=data["valeurs"][:-1],
            mode="lines+markers",
            name=f"Chap. {chapitre}",
            line=dict(color=couleur, width=2),
            marker=dict(size=7, color=couleur),
            hovertemplate=(
                f"<b>Chapitre {chapitre}</b><br>"
                "%{x} : %{y:,.2f} €<extra></extra>"
            )
        ))

        # Segment N+1 en pointillés
        fig.add_trace(go.Scatter(
            x=[labels[-2], labels[-1]],
            y=[data["valeurs"][-2], data["valeurs"][-1]],
            mode="lines+markers",
            name=f"Chap. {chapitre} (proj.)",
            line=dict(color=couleur, width=2, dash="dot"),
            marker=dict(size=9, color=couleur, symbol="diamond"),
            showlegend=False,
            hovertemplate=(
                f"<b>Chapitre {chapitre} — Projection</b><br>"
                "%{x} : %{y:,.2f} €<extra></extra>"
            )
        ))

    # ✅ Ligne verticale via add_shape (compatible axe catégoriel)
    fig.add_shape(
        type="line",
        xref="x", yref="paper",
        x0=labels[-2], x1=labels[-2],
        y0=0, y1=1,
        line=dict(color="rgba(255,255,255,0.3)", dash="dash", width=1.5)
    )
    fig.add_annotation(
        x=labels[-2], y=1.02,
        xref="x", yref="paper",
        text="◀ Réalisé | Projeté ▶",
        showarrow=False,
        font=dict(color="rgba(255,255,255,0.6)", size=11),
        xanchor="center"
    )

    fig.update_layout(
        title=dict(text=titre, font=dict(size=16)),
        xaxis=dict(title="Exercice", tickfont=dict(size=12)),
        yaxis=dict(title="Montant (€)", tickformat=",.0f", ticksuffix=" €"),
        legend=dict(
            orientation="h",
            yanchor="bottom", y=-0.3,
            xanchor="center", x=0.5,
            font=dict(size=11)
        ),
        hovermode="x unified",
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        margin=dict(l=60, r=20, t=60, b=80),
        height=500
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.1)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.1)")

    return fig


def graphique_tendance_chapitre(chapitre, data, couleur="#4E79A7"):
    """Graphique Plotly pour un seul chapitre."""
    labels  = data["labels"]
    valeurs = data["valeurs"]

    r = int(couleur[1:3], 16)
    g = int(couleur[3:5], 16)
    b = int(couleur[5:7], 16)

    fig = go.Figure()

    # Zone remplie historique + N
    fig.add_trace(go.Scatter(
        x=labels[:-1],
        y=valeurs[:-1],
        mode="lines+markers",
        name="Réalisé / Prévu",
        line=dict(color=couleur, width=2.5),
        marker=dict(size=8, color=couleur),
        fill="tozeroy",
        fillcolor=f"rgba({r},{g},{b},0.15)",
        hovertemplate="%{x} : %{y:,.2f} €<extra></extra>"
    ))

    # Segment N+1 en pointillés
    fig.add_trace(go.Scatter(
        x=[labels[-2], labels[-1]],
        y=[valeurs[-2], valeurs[-1]],
        mode="lines+markers",
        name="Projection N+1",
        line=dict(color="#F28E2B", width=2.5, dash="dot"),
        marker=dict(size=10, color="#F28E2B", symbol="diamond"),
        hovertemplate="Projection %{x} : %{y:,.2f} €<extra></extra>"
    ))

    # Annotation valeur N+1
    fig.add_annotation(
        x=labels[-1], y=valeurs[-1],
        text=f"  {valeurs[-1]:,.0f} €",
        showarrow=False,
        font=dict(color="#F28E2B", size=11),
        xanchor="left"
    )

    # ✅ Ligne verticale via add_shape (compatible axe catégoriel)
    fig.add_shape(
        type="line",
        xref="x", yref="paper",
        x0=labels[-2], x1=labels[-2],
        y0=0, y1=1,
        line=dict(color="rgba(255,255,255,0.2)", dash="dash", width=1)
    )

    fig.update_layout(
        title=dict(text=f"Tendance — Chapitre {chapitre}", font=dict(size=13)),
        xaxis=dict(tickfont=dict(size=11)),
        yaxis=dict(tickformat=",.0f", ticksuffix=" €"),
        legend=dict(orientation="h", y=-0.25, x=0.5, xanchor="center"),
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        font=dict(color="white"),
        margin=dict(l=60, r=40, t=40, b=60),
        height=320,
        hovermode="x"
    )
    fig.update_xaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)")
    fig.update_yaxes(showgrid=True, gridcolor="rgba(255,255,255,0.08)")

    return fig


def afficher_graphiques_tendance(df, chapitres_data):
    """
    Affiche :
    1. Graphique global (tous chapitres)
    2. Graphiques individuels dans des expanders
    """
    st.divider()
    st.subheader("📈 Tendance budgétaire N-4 → N+1")

    series = construire_series_tendance(df, chapitres_data)

    if not series:
        st.warning("Données historiques insuffisantes pour afficher la tendance.")
        return

    # Graphique global
    st.markdown("### 🌐 Vue globale — Tous chapitres")
    fig_global = graphique_tendance_global(series)
    # ✅ key unique
    st.plotly_chart(fig_global, use_container_width=True, key="tendance_global")

    # Graphiques par chapitre
    st.markdown("### 🔍 Détail par chapitre")

    for i, (chapitre, data) in enumerate(series.items()):
        edited_df = chapitres_data[chapitre]
        libelle   = ""
        if "Libelle_budget" in edited_df.columns:
            libelle = edited_df["Libelle_budget"].iloc[0]

        titre_exp = f"📊 Chapitre {chapitre}"
        if libelle:
            titre_exp += f" – {libelle}"

        with st.expander(titre_exp, expanded=False):
            couleur = COULEURS[i % len(COULEURS)]
            fig_ch  = graphique_tendance_chapitre(chapitre, data, couleur)
            # ✅ key unique par chapitre
            st.plotly_chart(
                fig_ch,
                use_container_width=True,
                key=f"tendance_global_ch_{chapitre}"
            )

            # Mini tableau synthèse avec variation % année par année
            labels  = data["labels"]
            valeurs = data["valeurs"]
            synth   = pd.DataFrame({
                "Exercice":    labels,
                "Montant (€)": valeurs
            })
            synth["Variation %"] = synth["Montant (€)"].pct_change().mul(100).round(2)
            synth["Variation %"] = synth["Variation %"].apply(
                lambda x: f"{x:+.2f} %" if pd.notna(x) else "—"
            )

            st.dataframe(
                synth,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Montant (€)": st.column_config.NumberColumn(format="%,.2f €")
                }
            )


# =====================================================
# PROJECTION N+1 — V6.1.0
# =====================================================

def projection_n_plus_1(df, taux_projection=3, mode_projection="Réaliste"):
    st.subheader("📈 Simulation N+1 — Compte par compte")

    if mode_projection == "Prudent":
        facteur = 1 + ((taux_projection - 1) / 100)
    elif mode_projection == "Optimiste":
        facteur = 1 + ((taux_projection + 1) / 100)
    else:
        facteur = 1 + (taux_projection / 100)

    variation_initiale = round((facteur - 1) * 100, 2)

    cols_requises = ["Chapitre", "Compte", "Total_Prevu", "Liquidé_N_1"]
    if not all(col in df.columns for col in cols_requises):
        st.warning(
            f"Colonnes nécessaires manquantes. "
            f"Requises : {cols_requises}. "
            f"Disponibles : {list(df.columns)}"
        )
        return

    group_cols_compte = [
        c for c in ["Section", "Sens", "Chapitre", "Libelle_budget", "Compte"]
        if c in df.columns
    ]

    # -------------------------------------------------------
    # ÉTAPE 1 — Édition compte par compte
    # -------------------------------------------------------
    st.markdown("### 🔍 Ajustement par compte")
    st.caption(
        "Modifiez la variation % compte par compte. "
        "Le chapitre et l'écart global sont recalculés automatiquement."
    )

    comptes_df = (
        df.groupby(group_cols_compte, as_index=False)
        .agg({"Liquidé_N_1": "sum", "Total_Prevu": "sum"})
        .rename(columns={"Liquidé_N_1": "Année N-1", "Total_Prevu": "Année N"})
    )
    comptes_df["Variation %"] = variation_initiale

    chapitres           = comptes_df["Chapitre"].unique()
    edited_par_chapitre = {}

    for idx, chapitre in enumerate(sorted(chapitres)):
        df_ch = comptes_df[comptes_df["Chapitre"] == chapitre].copy()

        libelle_ch = ""
        if "Libelle_budget" in df.columns:
            mask = df["Chapitre"] == chapitre
            if mask.any():
                libelle_ch = df.loc[mask, "Libelle_budget"].iloc[0]

        titre_expander = f"📂 Chapitre {chapitre}"
        if libelle_ch:
            titre_expander += f" – {libelle_ch}"

        total_n_ch = df_ch["Année N"].sum()

        with st.expander(titre_expander, expanded=False):
            st.caption(f"💼 Total Année N : {total_n_ch:,.2f} €")

            edited = st.data_editor(
                df_ch,
                use_container_width=True,
                num_rows="fixed",
                column_config={
                    "Variation %": st.column_config.NumberColumn(
                        "Variation %",
                        step=0.5,
                        min_value=-100.0,
                        max_value=100.0,
                        format="%.2f %%"
                    ),
                    "Année N-1":      st.column_config.NumberColumn(format="%,.2f €", disabled=True),
                    "Année N":        st.column_config.NumberColumn(format="%,.2f €", disabled=True),
                    "Compte":         st.column_config.TextColumn(disabled=True),
                    "Chapitre":       st.column_config.TextColumn(disabled=True),
                    "Section":        st.column_config.TextColumn(disabled=True),
                    "Sens":           st.column_config.TextColumn(disabled=True),
                    "Libelle_budget": st.column_config.TextColumn(disabled=True),
                },
                key=f"editor_compte_{chapitre}"
            )

            # Calcul N+1 après édition
            edited["Variation %"] = edited["Variation %"].astype(float)
            edited["Année N+1"]   = (
                edited["Année N"] * (1 + edited["Variation %"] / 100.0)
            ).round(2)

            # Résultat comptes en lecture seule
            st.markdown("##### 📋 Résultat N+1 par compte")
            st.dataframe(
                edited,
                use_container_width=True,
                column_config={
                    "Variation %": st.column_config.NumberColumn(format="%.2f %%"),
                    "Année N-1":   st.column_config.NumberColumn(format="%,.2f €"),
                    "Année N":     st.column_config.NumberColumn(format="%,.2f €"),
                    "Année N+1":   st.column_config.NumberColumn(format="%,.2f €"),
                }
            )

            # Synthèse du chapitre
            total_n_ch_calc  = edited["Année N"].sum()
            total_n1_ch_calc = edited["Année N+1"].sum()
            variation_ch     = safe_div(
                (total_n1_ch_calc - total_n_ch_calc) * 100, total_n_ch_calc
            )

            cc1, cc2, cc3 = st.columns(3)
            with cc1:
                badgeBlue("Total N chapitre", round(total_n_ch_calc, 2))
            with cc2:
                badgeGreen("Total N+1 chapitre", round(total_n1_ch_calc, 2))
            with cc3:
                badge("Variation chapitre (%)", round(variation_ch, 2))

            # Mini graphique tendance dans l'expander
            st.markdown("##### 📈 Tendance du chapitre")
            series_ch = construire_series_tendance(df, {chapitre: edited})
            if chapitre in series_ch:
                couleur  = COULEURS[idx % len(COULEURS)]
                fig_mini = graphique_tendance_chapitre(
                    chapitre, series_ch[chapitre], couleur
                )
                # ✅ key unique mini graphique
                st.plotly_chart(
                    fig_mini,
                    use_container_width=True,
                    key=f"tendance_mini_ch_{chapitre}"
                )

        edited_par_chapitre[chapitre] = edited

    # -------------------------------------------------------
    # ÉTAPE 2 — Récapitulatif chapitres recalculé
    # -------------------------------------------------------
    st.divider()
    st.markdown("### 📊 Récapitulatif par chapitre — recalculé")

    lignes_chapitres = []
    for chapitre, edited in edited_par_chapitre.items():
        total_n_1_ch = edited["Année N-1"].sum()
        total_n_ch   = edited["Année N"].sum()
        total_n1_ch  = edited["Année N+1"].sum()
        variation_ch = round(
            safe_div((total_n1_ch - total_n_ch) * 100, total_n_ch), 2
        )
        row = {"Chapitre": chapitre}
        if "Section"        in edited.columns: row["Section"]        = edited["Section"].iloc[0]
        if "Sens"           in edited.columns: row["Sens"]           = edited["Sens"].iloc[0]
        if "Libelle_budget" in edited.columns: row["Libelle_budget"] = edited["Libelle_budget"].iloc[0]
        row["Année N-1"]   = round(total_n_1_ch, 2)
        row["Année N"]     = round(total_n_ch, 2)
        row["Variation %"] = variation_ch
        row["Année N+1"]   = round(total_n1_ch, 2)
        lignes_chapitres.append(row)

    recap_df = pd.DataFrame(lignes_chapitres)

    st.dataframe(
        recap_df,
        use_container_width=True,
        column_config={
            "Variation %": st.column_config.NumberColumn(format="%.2f %%"),
            "Année N-1":   st.column_config.NumberColumn(format="%,.2f €"),
            "Année N":     st.column_config.NumberColumn(format="%,.2f €"),
            "Année N+1":   st.column_config.NumberColumn(format="%,.2f €"),
        }
    )

    # -------------------------------------------------------
    # ÉTAPE 3 — Totaux globaux + card écart
    # -------------------------------------------------------
    total_n_global   = recap_df["Année N"].sum()
    total_n1_global  = recap_df["Année N+1"].sum()
    evolution_global = round(
        safe_div((total_n1_global - total_n_global) * 100, total_n_global), 2
    )

    c1, c2, c3 = st.columns(3)
    with c1:
        badgeBlue("Total N global", round(total_n_global, 2))
    with c2:
        badgeGreen("Total N+1 global", round(total_n1_global, 2))
    with c3:
        badge("Évolution globale (%)", evolution_global)

    st.success(f"💡 Total projeté N+1 global : {total_n1_global:,.2f} €")

    # Card écart budgétaire
    afficher_ecart_budgetaire(total_n_global, total_n1_global)

    # -------------------------------------------------------
    # ÉTAPE 4 — Graphiques tendance globaux
    # -------------------------------------------------------
    afficher_graphiques_tendance(df, edited_par_chapitre)


# =====================================================
# MAIN
# =====================================================

def main():
    afficher_header()

    with st.sidebar:
        st.markdown("## 📋 Menu de pilotage")
        st.caption("Chargement, filtres et simulation")
        st.divider()

        with st.expander("📂 Chargement des données", expanded=True):
            file = st.file_uploader("📁 Fichier CSV", type="csv")

        if not file:
            st.info("⬅️ Chargez un fichier CSV")
            st.stop()

        try:
            df, annees = load_csv(file)
            df = normaliser_colonnes(df)
            df = harmoniser_colonnes(df)
        except Exception as e:
            st.error(f"Erreur chargement : {e}")
            st.stop()

        st.divider()
        with st.expander("🔎 Filtres budgétaires", expanded=True):
            budget, section, sens, population = filtres(df)

        st.divider()
        with st.expander("📈 Simulation et scénarios", expanded=False):
            taux_projection = st.slider("Projection N+1 (%)", -10, 15, 3, 1)
            mode_projection = st.selectbox(
                "Scénario",
                ["Prudent", "Réaliste", "Optimiste"],
                index=1
            )

    df_filtre = df[
        (df["Libelle_budget"] == budget)
        & (df["Section"] == section)
        & (df["Sens"] == sens)
    ]

    if df_filtre.empty:
        st.warning("⚠️ Aucun résultat avec ces filtres")
        st.stop()

    sommes, report_f, report_i = calculer_sommes_par_chapitre(df_filtre, annees)

    total_budget  = safe_sum(df_filtre, "Total_Prevu")
    total_realise = safe_sum(df_filtre, "Realise")
    reste_engage  = safe_sum(df_filtre, "Reste_engage")

    if section == "F" and sens == "R":
        total_realise -= report_f
    elif section == "I" and sens == "R":
        total_realise -= report_i

    ratio = safe_div(total_realise + reste_engage, population)
    taux  = safe_div(total_realise * 100, total_budget)

    afficher_indicateurs(total_budget, total_realise, reste_engage, ratio, taux)

    st.divider()
    tableau_chapitres(df_filtre, budget=budget, section=section, sens=sens)

    st.divider()
    camembert(df_filtre)

    st.divider()
    st.subheader(f"💰 Auto-financement ({budget})")
    auto = calcul_autofinancement(df, budget)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    with c1:
        badge("Marge brute",               auto.get("Marge brute", 0))
    with c2:
        badge("Épargne brute",             auto.get("Epargne brute", 0))
    with c3:
        badgeRed("Produits exceptionnels", auto.get("Dont produits exceptionnels", 0))
    with c4:
        badgeGreen("Épargne nette",        auto.get("Epargne nette", 0))
    with c5:
        badgeBlue("Report N-1",            auto.get("Report N -1", 0))
    with c6:
        badgeGreen("Disponibilité",        auto.get("Disponibilite", 0))

    st.divider()
    analyse_budget(total_budget, total_realise, reste_engage, ratio)

    st.divider()
    projection_n_plus_1(df_filtre, taux_projection, mode_projection)


if __name__ == "__main__":
    main()