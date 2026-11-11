import json

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy import func, select

from config import get_settings
from src.database.crud import seed_demo_data
from src.database.models import AIInsight, Competitor, PriceHistory, Product, Promotion
from src.database.queries import product_price_rows
from src.database.session import get_session_factory
from src.tasks.celery_app import app
from src.tasks.monitoring import collect_monitoring, run_full_monitoring


def sidebar(session_factory) -> None:
    with session_factory() as session:
        c_count = session.scalar(select(func.count()).select_from(Competitor)) or 0
        p_count = session.scalar(select(func.count()).select_from(Product)) or 0
        ph_count = session.scalar(select(func.count()).select_from(PriceHistory)) or 0
        pr_count = session.scalar(select(func.count()).select_from(Promotion)) or 0

    st.sidebar.header("Сводка системы")
    st.sidebar.metric("Конкуренты", c_count)
    st.sidebar.metric("Товары", p_count)
    st.sidebar.metric("Срезы цен", ph_count)
    st.sidebar.metric("Акции и посты", pr_count)


def prices_tab(session_factory) -> None:
    with session_factory() as session:
        rows = product_price_rows(session)
    if not rows:
        st.info("Запустите мониторинг во вкладке «Управление», чтобы собрать цены.")
        return

    products = {
        row.id: f"{row.competitor} · {row.title} ({row.external_id})"
        for row in rows
    }
    product_id = st.selectbox("Товар", list(products), format_func=products.__getitem__)
    query = (
        select(PriceHistory.timestamp, PriceHistory.price, PriceHistory.is_demo)
        .where(PriceHistory.product_id == product_id)
        .order_by(PriceHistory.timestamp.desc(), PriceHistory.id.desc())
        .limit(500)
    )
    with session_factory() as session:
        prices = pd.read_sql(query, session.connection())
    if prices.empty:
        st.info("По этому товару пока нет наблюдений.")
    else:
        prices["source"] = prices["is_demo"].map({True: "Демо", False: "Реальные"})
        st.plotly_chart(px.line(
            prices.sort_values("timestamp", kind="stable"), x="timestamp", y="price",
            color="source", markers=True,
            labels={"timestamp": "Дата", "price": "Цена", "source": "Данные"},
        ))

    table = []
    for row in rows:
        delta = row.price - row.previous_price if row.price is not None and row.previous_price is not None else None
        table.append({
            "Конкурент": row.competitor,
            "Товар": row.title,
            "Категория": row.category,
            "Последняя цена": float(row.price) if row.price is not None else None,
            "Предыдущая цена": float(row.previous_price) if row.previous_price is not None else None,
            "Δ цена": float(delta) if delta is not None else None,
            "Δ %": float(delta * 100 / row.previous_price) if delta is not None and row.previous_price else None,
            "Данные": ("Демо" if row.is_demo else "Реальные") if row.price is not None else "—",
        })
    st.dataframe(table, hide_index=True, column_config={
        "Δ цена": st.column_config.NumberColumn(format="%+.2f"),
        "Δ %": st.column_config.NumberColumn(format="%+.2f%%"),
    })


def promotions_tab(session_factory) -> None:
    with session_factory() as session:
        names = dict(session.execute(
            select(Competitor.id, Competitor.name).order_by(Competitor.name, Competitor.id)
        ).all())
        selected = st.multiselect("Конкуренты", list(names), format_func=names.__getitem__)
        query = select(Promotion).order_by(Promotion.timestamp.desc(), Promotion.id.desc()).limit(100)
        if selected:
            query = query.where(Promotion.competitor_id.in_(selected))
        promotions = list(session.scalars(query))
    if not promotions:
        st.info("Для выбранных конкурентов пока нет акций и постов.")
    for promotion in promotions:
        with st.container(border=True):
            st.caption(
                f"{names.get(promotion.competitor_id, '—')} · {promotion.timestamp:%d.%m.%Y %H:%M %Z} · "
                f"{'Telegram' if promotion.source == 'tg' else 'Сайт'} · {promotion.promo_type or 'акция'}"
            )
            st.text(promotion.summary or promotion.raw_text)


def insights_tab(session_factory) -> None:
    with session_factory() as session:
        insight = session.scalar(
            select(AIInsight).order_by(AIInsight.created_at.desc(), AIInsight.id.desc()).limit(1)
        )
    if insight is None:
        st.info("Отчёт появится после первого запуска мониторинга.")
        return

    try:
        report = json.loads(insight.content)
    except ValueError:
        report = {}
    if not isinstance(report, dict):
        report = {}
    st.caption(f"{insight.created_at:%d.%m.%Y %H:%M %Z}")
    st.subheader("Резюме")
    st.text(report.get("summary", insight.content))
    if report.get("trends"):
        st.subheader("Тренды")
        for trend in report["trends"]:
            st.text(f"• {trend}")

    priorities = {"high": "Высокий", "medium": "Средний", "low": "Низкий"}
    if insight.recommendations_json:
        st.subheader("Рекомендации")
        for item in insight.recommendations_json:
            if not isinstance(item, dict):
                continue
            priority = item.get("priority", "—")
            with st.container(border=True):
                st.caption(f"Приоритет · {priorities.get(priority, priority)}")
                st.text(item.get("action", ""))
                st.text(item.get("reason", ""))
                st.text(f"Целевая метрика · {item.get('target_metric', '—')}")


def management_tab(session_factory) -> None:
    if st.button("Добавить демо-конкурентов"):
        with session_factory.begin() as session:
            count = seed_demo_data(session)
        st.session_state["seed_message"] = (
            f"Добавлено конкурентов: {count}." if count else "В базе уже есть конкуренты."
        )
        st.rerun()
    if message := st.session_state.pop("seed_message", None):
        st.info(message)

    if get_settings().local_demo:
        st.caption("Локальный демо-режим · SQLite")
        if st.button("Запустить мониторинг и сформировать отчёт", type="primary"):
            with st.spinner("Сбор демо-данных…"):
                st.session_state["local_monitoring_result"] = collect_monitoring(
                    session_factory, force_report=True
                )
            st.rerun()
        if details := st.session_state.get("local_monitoring_result"):
            st.write(f"Срезов цен: {details['price_snapshots']}. Отчёт: {details['insight_id'] or '—'}.")
            for issue in details["issues"]:
                st.warning(issue)
        return

    if st.button("Запустить мониторинг и сформировать отчёт", type="primary"):
        try:
            task = run_full_monitoring.apply_async(kwargs={"force_report": True}, expires=1800, retry=False)
        except Exception:
            st.error("Не удалось поставить задачу в очередь. Проверьте подключение к Redis.")
        else:
            st.session_state["monitoring_task_id"] = task.id

    if task_id := st.session_state.get("monitoring_task_id"):
        st.button("Обновить статус и данные")
        try:
            result = app.AsyncResult(task_id)
            status = result.state
            details = result.result if status == "SUCCESS" else None
        except Exception:
            st.error("Хранилище результатов Celery недоступно.")
            return
        st.caption(f"Задача {task_id} · {status}")
        if status == "SUCCESS" and details["status"] == "already_running":
            st.info("Мониторинг уже запущен другой задачей.")
        elif status == "SUCCESS":
            st.write(
                f"Срезов цен: {details['price_snapshots']}. Снижений более 10%: {details['price_drops']}. "
                f"Алертов отправлено: {details['alerts_sent']}."
            )
            for issue in details["issues"]:
                st.warning(issue)
        elif status in {"FAILURE", "REVOKED"}:
            st.error("Задача завершилась без результата. Подробности в журнале Celery.")


def main() -> None:
    st.set_page_config(page_title="Мониторинг конкурентов", layout="wide")
    st.title("Мониторинг конкурентов")
    session_factory = get_session_factory()
    sidebar(session_factory)
    tabs = st.tabs(["Аналитика цен", "Акции и соцсети", "AI Инсайты и рекомендации", "Управление"])
    for tab, render in zip(tabs, (prices_tab, promotions_tab, insights_tab, management_tab)):
        with tab:
            render(session_factory)


main()
