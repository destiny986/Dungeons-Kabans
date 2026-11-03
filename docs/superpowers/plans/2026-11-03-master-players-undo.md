# Master players and bounded undo Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Обновить панель игроков и добавить подтверждаемую последовательную отмену 10 последних игровых операций.

**Architecture:** Отдельный ограниченный UndoEntry связан с долговечной Operation. Существующие атомарные операции сохраняют точные обратные изменения, отдельный модуль проверяет и восстанавливает их; HTTP использует двухшаговое подтверждение и существующий OOB polling.

**Tech Stack:** Python 3.12, Django 5.2, SQLite IMMEDIATE, templates, локальные HTMX/GridStack.

**Spec:** `docs/superpowers/specs/2026-11-03-master-players-undo-design.md` (подтверждён пользователем).

## Global Constraints

- Основная папка `C:\dev\Dungeons-Kabans`; без worktree, публикации и production.
- Русский интерфейс, существующие права game.access, POST/CSRF, короткие транзакции.
- Лимит 10 общих неотменённых операций; Operation не обрезается, ZIP не содержит журнал.
- Перезапуск и очистка чата сохраняют журнал; импорт и очистка игровых данных сбрасывают его.
- Сохранять черновики, фокус, открытое подтверждение, UUID при ошибках, мобильную доступность.

## Review Focus

- Отмена новой операции повышает revision, но не должна блокировать непрерывную старую цепочку (Task 1: тест цепочки и промежуточного здоровья).
- Повтор исходного запроса после отмены не возвращает изменения (Task 1: replay обоих запросов).
- Пока подтверждение открыто, новая вершина не становится его целью (Task 2: stale target и два браузера).
- Очистка чата не удаляет сведения о нескольких сообщениях массовой выдачи (Task 1/2: clear then undo).
- Каталог действия редактируется после расхода; возвращается сохранённый вид ресурса (Task 1: historical resource).

### Task 1: Ограниченный журнал и атомарная отмена

**Files:** create `game/undo.py`, `game/test_undo.py`, migration; modify `game/models.py`, `game/operations.py`.

**Interfaces:** `UndoEntry(operation, kind, description, payload)` с упорядоченным BigAutoField;
`game.undo.record(operation, kind, description, effects, message_ids)`,
`game.undo.restore(entry)` (конфликт отдельным исключением);
`game.operations.undo_last(request, entry_id, request_id)` возвращает стандартный result;
`undo_preview(request, entry_id)` возвращает confirm result без записи операции.

- [x] Написать тесты всех пяти обратных изменений, групповых выдач/дубликатов, лимита 10/11, replay, цепочки и конфликтов, числовых границ, удалённых сущностей, исторического ресурса, подтверждений предложенной ячейки, приватных сообщений и авторизации.
- [x] Запустить `.\.venv\Scripts\python.exe -m pytest game/test_undo.py -q`, убедиться в ожидаемом отсутствии журнала/отмены.
- [x] Создать модель/миграцию и реализацию. Payload хранит типизированные эффекты, версии до/после и поколение доступа, точные значения и все message IDs. `_complete` сохраняет журнал в общей транзакции. При отмене сначала валидировать все эффекты, затем восстановить и повысить revision; предыдущую ожидаемую версию продвигать только через непрерывную цепочку.
- [x] Прогнать `game/test_undo.py game/test_operations.py`, ожидается PASS.
- [x] Commit `feat: record and undo last ten game operations`.

### Task 2: HTTP, сброс при переносе данных и панель игроков

**Files:** modify `game/web/views.py`, `game/web/data.py`, `game/archives/service.py`, `game/urls.py`, `game/templates/game/master.html`, `master_players.html`, `active_character.html`, `player_stats.html`, `game/static/game/app.css`; create `_ability_grid.html`, `undo_confirm.html`, `undo_confirm_page.html`; extend `game/test_undo.py`, `game/test_web.py`, `game/test_archives.py`.

**Interfaces:** POST `game:undo_last` `/master/undo/`, fields `entry_id`, `request_id`, optional `confirmed=1`. First POST returns centered escaped confirmation; confirmed POST calls Task 1 service. `master_context` includes `undo_entry`; card context includes six ability rows.

- [x] Написать HTTP-тесты no-write preview, no-JS fallback, POST/CSRF/master-only, stale target, replay, clear-chat survival, wipe/import reset; проверить новую навигацию и карточку.
- [x] Запустить область тестов, ожидается FAIL из-за отсутствия маршрута/интерфейса/сброса.
- [x] Добавить endpoint и подтверждение с сохранённым UUID/entry_id. Сброс на успешном импорте внутри сервиса и wipe внутри транзакции, без сброса на повторе уже завершённого импорта. Список/нижняя кнопка внутри существующего poll target, список скроллится отдельно. Общий partial характеристик. + справа от отдыха, удалить ⋮, адаптивные мини-карточки, нейтральные нижние кнопки.
- [x] Прогнать `game/test_undo.py game/test_web.py game/test_archives.py`, ожидается PASS.
- [x] Commit `feat: expose confirmed undo and refresh master players panel`.

### Task 3: Проверка приложения и контрактов

**Files:** update `docs/contracts.md`, `docs/operation-contracts.md`, `docs/web-contracts.md`, `docs/ui-contracts.md`, `docs/archive-format.md`, `docs/verification.md`; browser artifacts только в `.browser-artifacts/master-undo/`.

**Interfaces:** схема и публичное поведение Task 1/2; без новых runtime-зависимостей.

- [x] Проверить конкурентную отмену и новую операцию на файловой тестовой SQLite; добавить регрессионный тест при выявленном дефекте.
- [x] Выполнить `pytest -q`, `manage.py check`, `manage.py makemigrations --check --dry-run`, `node --check game/static/game/app.js`, `node tests/import_bulk.test.cjs`, `git diff --check`.
- [x] В отдельной базе проверить браузером desktop/mobile, resize панели, длинные имена, портрет/шесть характеристик, отзыв/редактирование, отмену подтверждения, массовую отмену и обновление чатов, сохранность черновика и stale confirmation.
- [x] Обновить контракты и журнал проверок; отметить spec approved. Commit `docs: document bounded undo and verification`.
- [x] Независимый финальный code review всей реализации; исправить существенные замечания с регрессионной проверкой, повторить затронутые проверки и зафиксировать итог.

## Execution ledger

Выполнять в текущем сеансе, разрешение пользователя «Подтверждаю, выполняй»
распространяется на план и реализацию; повторное согласование не требуется.
Служебный ledger: `.superpowers/sdd/2026-11-03-master-players-undo/progress.md`.
