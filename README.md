# TestHub AI-Powered Test Management Platform

**English** | [简体中文](README.zh-CN.md)

<div align="center">

**An AI-powered, full-stack platform for end-to-end software testing**

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-4.2-green.svg)](https://www.djangoproject.com/)
[![Vue](https://img.shields.io/badge/Vue-3.3-brightgreen.svg)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-GPL_v3-blue.svg)](LICENSE)

</div>

## Overview

TestHub is an AI-powered, full-stack test management platform that supports the complete software testing lifecycle. It brings together **AI requirement analysis and test case generation**, **test case management and reviews**, **API testing**, **Web UI automation**, **Android app automation**, **performance testing**, and a **test data factory** to help teams improve testing efficiency.

## Features

### AI capabilities

- **Requirement analysis:** Upload PDF, Word, or TXT documents and use AI to extract business requirements and generate test cases.
- **AI assistant:** Integrate with Dify to provide testing guidance and answer questions.
- **Intelligent browser automation:** Use Browser Use to understand pages and complete testing tasks in text or visual mode.
- **Multiple model providers:** Configure providers such as DeepSeek, Qwen, SiliconFlow, OpenAI, Anthropic, and Google Gemini.

### Test case management and reviews

- Manage the full test case lifecycle, including creation, editing, versioning, and archiving.
- Organize cases by project, version, and tags, with structured preconditions, steps, and expected results.
- Collaborate through attachments and team comments.
- Run reviews with multiple reviewers, templates, checklists, threaded review comments, and status tracking.

### API testing

- Test HTTP and WebSocket APIs with tree-structured collections, environments, and variable substitution.
- Run test suites in batches with configurable assertions and execution order.
- Review request history, schedule tasks with email or webhook notifications, and generate Allure reports.

### Web UI automation

- Choose Selenium or Playwright, with support for Chrome, Firefox, and Edge.
- Manage elements with multiple locator strategies and organize code using the Page Object Model (POM).
- Edit scripts visually, run suites in batches, and capture logs, screenshots, and video recordings.
- Schedule runs with cron expressions, fixed intervals, or one-time execution.
- Use AI mode to understand page structure and carry out testing tasks.
- Watch live browser views for headed case runs, script replays, and suite runs in Docker, with separate views for concurrent users.

### Android app automation

- Use Airtest image recognition with local emulators or remote devices; device locking helps avoid resource conflicts.
- Manage image, coordinate, and region-based elements with multi-resolution support.
- Build reusable components and UI Flow workflows, with global, local, and output variables.
- Execute tasks asynchronously with Django-Q2, generate Allure reports, and track progress over WebSocket.

### Performance testing

- Manage and run Locust load tests, and review performance metrics and reports.

### Test data factory

- Access more than 50 utilities for text processing, encoding, random data, encryption and decryption, test data generation, JSON processing, and cron expressions.
- Organize data with tags and usage history, and reference it directly in API and UI tests.

### Security and collaboration

- Use JWT access and refresh tokens with automatic renewal, logout blacklisting, and replay protection.
- Manage multiple projects, member roles and permissions, and release plans.
- Send unified notifications through email and WeCom, DingTalk, or Feishu webhook bots.

## Technology Stack

### Backend

- Django 6.0 and Django REST Framework
- MySQL 8.0+
- JWT authentication with `rest_framework_simplejwt` and token blacklisting
- Selenium, Playwright, Airtest, OCR, and Allure
- Django-Q2, APScheduler, Channels, and Daphne for asynchronous work and WebSockets
- `browser-use`, `langchain-openai`, and multiple AI model providers
- `drf-spectacular` for Swagger and ReDoc API documentation

### Frontend

- Vue 3, Vite, and Element Plus
- Pinia, Vue Router, and Axios
- ECharts, Monaco Editor, and vue-i18n

## Project Structure

```text
testhub_platform/
├── apps/                           # Django application modules
│   ├── users/                      # Authentication and user management
│   ├── projects/                   # Project management
│   ├── testcases/                  # Test case management
│   ├── testsuites/                 # Test suites
│   ├── executions/                 # Test plans and execution
│   ├── reports/                    # Test reports
│   ├── defects/                    # Defect management
│   ├── reviews/                    # Test case reviews
│   ├── versions/                   # Version management
│   ├── core/                       # Shared commands, variable resolution, notifications
│   ├── api_testing/                # API testing
│   ├── ui_automation/              # Web UI automation, including AI mode
│   ├── app_automation/             # Android app automation
│   ├── perf_testing/               # Performance testing
│   ├── requirement_analysis/        # AI requirement analysis
│   ├── assistant/                  # Dify assistant
│   ├── data_factory/               # Test data factory
│   └── llm_judge/                  # LLM evaluation
├── backend/                        # Django settings, URLs, and ASGI configuration
├── frontend/                       # Vue 3 frontend
├── media/                          # Uploaded files and screenshots
├── logs/                            # Log files
├── allure/                          # Allure reporting tools
├── requirements.txt                # Python dependencies
└── manage.py                       # Django management script
```

## Quick Start

### Prerequisites

- **Python:** 3.12 recommended; other versions may have compatibility issues.
- **Node.js:** 18+ for frontend builds; not required on production servers serving prebuilt assets.
- **MySQL:** 8.0+
- **Java:** 17+ (optional; required to generate Allure reports).
- **Redis:** 6.0+ (optional; required for app automation WebSockets).
- **Browser drivers:** ChromeDriver or GeckoDriver for UI automation; these can also be downloaded with a management command.

### Backend setup

1. Clone the repository:

   ```bash
   git clone <repository-url>
   cd testhub_platform
   ```

2. Create a virtual environment and install dependencies:

   ```bash
   python -m venv venv
   # Windows
   venv\Scripts\activate
   # Linux / macOS
   source venv/bin/activate

   pip install -r requirements.txt
   ```

3. Configure environment variables:

   ```bash
   # Update database, Redis, email, and other settings as needed.
   cp .env.example .env
   ```

4. Initialize the database:

   ```bash
   # Create the database.
   mysql -u root -p -e "CREATE DATABASE testhub CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"

   # Apply migrations and create an administrator account.
   python manage.py makemigrations
   python manage.py migrate
   python manage.py createsuperuser
   ```

5. Optionally initialize module data:

   ```bash
   # UI automation locator strategies
   python manage.py init_locator_strategies
   # APP automation component pack
   python manage.py load_component_pack
   ```

6. Start the services you need:

   ```bash
   # Django development server (HTTP only)
   python manage.py runserver

   # Use Daphne instead when APP automation WebSocket progress is required.
   daphne -b 0.0.0.0 -p 8000 backend.asgi:application

   # Optional: API/UI scheduled task scheduler
   python manage.py run_all_scheduled_tasks

   # Django-Q2 worker for asynchronous tasks
   python manage.py qcluster
   ```

### Frontend setup

```bash
cd frontend
npm install
npm run dev        # Development server
npm run build      # Production build
```

### Application URLs

- **Frontend:** http://localhost:3000
- **Backend API:** http://localhost:8000
- **API documentation:** http://localhost:8000/api/docs/
- **Django admin:** http://localhost:8000/admin/

### Docker deployment (optional)

From the repository root, copy `.env.example` to `.env` and set at least
`SECRET_KEY`, `DB_PASSWORD`, and the allowed hosts/origins for the address where
the application will be accessed. The Docker image runs Nginx, Daphne, Django-Q2,
the scheduler, MySQL, and Redis in one container:

```bash
docker compose --env-file .env -f docker/docker-compose.yml up -d --build
docker compose --env-file .env -f docker/docker-compose.yml logs -f testhub

# Create the first administrator after the container is healthy.
docker compose --env-file .env -f docker/docker-compose.yml exec testhub python manage.py createsuperuser
```

For a multi-platform image, use `bash docker/build-multiplatform.sh --push` after
setting `DOCKER_REGISTRY`, `DOCKER_NAMESPACE`, and `IMAGE_VERSION`.

## Configuration

Configure environment variables in `.env` (see `.env.example`). Common settings include:

- **Database:** `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, and `DB_PORT`.
- **Redis:** `REDIS_URL` for APP automation WebSockets.
- **AI models:** Configure API keys, base URLs, and model parameters for multiple providers in the unified configuration center. Settings can be assigned to roles such as test case writer, reviewer, and Browser Use, with connection testing available.
- **UI automation:** Select the Selenium or Playwright engine, browser, and headed or headless mode.
- **Notifications:** Configure SMTP email and WeCom, DingTalk, or Feishu webhook bots.

## Documentation

See [docs/docs-center](./docs/docs-center/) for more guides:

- [Data factory guide](./docs/docs-center/数据工厂使用说明.md), [quick start](./docs/docs-center/数据工厂快速开始.md), [feature guide](./docs/docs-center/数据工厂功能说明.md), and [API reference](./docs/docs-center/数据工厂API接口文档.md)
- [UI automation execution guide](./docs/docs-center/UI自动化测试执行说明.md) and [user manual](./docs/docs-center/UI%20自动化测试用户使用手册.md)
- [WebDriver management improvements](./docs/docs-center/WebDriver驱动管理优化说明.md)
- [Test case review guide](./docs/docs-center/用例评审管理功能说明.md)
- [Internationalization guide](./docs/docs-center/I18N国际化使用说明.md)
- [Troubleshooting guide](./docs/docs-center/问题排查指南.md)

## Contributing

Issues and pull requests are welcome.

1. Fork this repository.
2. Create a feature branch (`git checkout -b feature/AmazingFeature`).
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`).
4. Push the branch (`git push origin feature/AmazingFeature`).
5. Open a pull request.

## License

This project is licensed under GPL-3.0. See the [LICENSE](LICENSE) file for details.
