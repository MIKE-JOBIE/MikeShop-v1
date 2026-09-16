# 👟 MikeShop V1

### Modern Sneaker Shop Management Web Application

MikeShop is a Flask-based web application designed to provide a simple, modern, and centralized management system for a sneaker retail business.

The application brings together inventory management, sales recording, sales analytics, expense accountability, staff management, restocking, notifications, receipts, and administrative controls into a single web-based dashboard.

**Version:** 1.0.0  
**Status:** Stable / Production Deployed  
**Application Type:** Web Application  
**Backend:** Python / Flask  
**Database:** SQLite  
**Hosting:** Render  
**Repository:** [github.com/MIKE-JOBIE/MikeShop](https://github.com/MIKE-JOBIE/MikeShop)

---

# 📌 Table of Contents

- [Overview](#-overview)
- [Project Goals](#-project-goals)
- [Core Features](#-core-features)
- [User Roles and Permissions](#-user-roles-and-permissions)
- [Dashboard](#-dashboard)
- [Inventory Management](#-inventory-management)
- [Sales Management](#-sales-management)
- [Sales Analytics and History](#-sales-analytics-and-history)
- [Expense Management and Accountability](#-expense-management-and-accountability)
- [Restocking](#-restocking)
- [Staff Management](#-staff-management)
- [Notifications](#-notifications)
- [Audit Logging](#-audit-logging)
- [Receipts and PDF Export](#-receipts-and-pdf-export)
- [Authentication and Security](#-authentication-and-security)
- [Technology Stack](#-technology-stack)
- [Project Architecture](#-project-architecture)
- [Project Structure](#-project-structure)
- [Database Models](#-database-models)
- [Configuration and Environment Variables](#-configuration-and-environment-variables)
- [Installation](#-installation)
- [Running Locally](#-running-locally)
- [Production Deployment](#-production-deployment)
- [Git and GitHub Workflow](#-git-and-github-workflow)
- [Database Considerations](#-database-considerations)
- [Security Guidelines](#-security-guidelines)
- [Current V1 Scope](#-current-v1-scope)
- [Known V1 Limitations](#-known-v1-limitations)
- [V2 Roadmap](#-v2-roadmap)
- [Development Principles](#-development-principles)
- [Contributing](#-contributing)
- [License](#-license)
- [Project Status](#-project-status)

---

# 📖 Overview

MikeShop V1 is an administrative management system developed for a sneaker retail business.

The application is intended to simplify daily shop operations by allowing authorized users to manage products, monitor inventory, record sales, track expenses, manage staff, monitor profitability, and review business activity from a centralized dashboard.

The V1 architecture intentionally focuses on the core sneaker-shop workflow rather than attempting to support every possible retail business function.

The application is designed to provide a foundation that can later be expanded into a broader retail management platform.

---

# 🎯 Project Goals

The primary goals of MikeShop V1 are:

1. Provide a centralized sneaker inventory system.
2. Make sales recording fast and reliable.
3. Track revenue and profit.
4. Monitor stock levels.
5. Provide low-stock warnings.
6. Provide sales analytics and history.
7. Make business expenses accountable.
8. Restrict sensitive operations according to user roles.
9. Provide controlled staff management.
10. Record important administrative activity.
11. Provide restocking functionality.
12. Provide receipts and sales exports.
13. Provide real-time dashboard notifications.
14. Maintain a clean and maintainable web application structure.
15. Provide a stable foundation for future versions.

---

# 🚀 Core Features

MikeShop V1 includes:

- 🔐 User authentication
- 👥 Role-based access control
- 📊 Management dashboard
- 👟 Sneaker inventory management
- 💰 Sales recording
- 📈 Sales analytics
- 🧾 Sales history
- 📄 PDF/receipt generation
- 💸 Expense management
- 👤 Expense accountability
- 📦 Restocking
- ⚠️ Low-stock alerts
- 🔔 Real-time notifications
- 📝 Audit logging
- 👨‍💼 Staff management
- 🔑 Password reset functionality
- 📤 Sales export
- 📑 Pagination
- 💱 USD/SLL conversion support
- 📱 Responsive web interface
- 🎨 Centralized CSS
- ⚙️ Centralized JavaScript
- ☁️ Render deployment support

---

# 👥 User Roles and Permissions

MikeShop V1 uses role-based access control.

The primary roles are:

| Role | Purpose |
|---|---|
| **Owner** | Full administrative control |
| **Admin** | Administrative operations with controlled permissions |
| **Staff** | Day-to-day operational access |

## Owner

The owner is the highest-level account.

Owner capabilities include management of:

- Staff
- Roles
- Inventory
- Sales
- Expenses
- Restocking
- Administrative functions
- Business records

The owner account is protected from normal staff-management operations.

---

## Admin

Administrators can perform designated administrative tasks.

In particular, V1 restricts expense creation to:

- Owner
- Admin

This ensures that expenses are not freely created by ordinary staff members.

---

## Staff

Staff accounts are intended for operational users.

Their permissions are intentionally more limited than Owner and Admin accounts.

The role system is designed so that future versions can expand permission granularity without redesigning the entire application.

---

# 📊 Dashboard

The dashboard serves as the central management interface.

It provides an overview of important business activity, including:

- Total sales
- Total profit
- Total expenses
- Net profit
- Inventory information
- Low-stock items
- Sales activity
- Best-selling products
- Recent records
- Notifications
- Expense accountability
- Staff management information

The dashboard is designed to provide management-level visibility without requiring users to navigate through multiple unrelated pages.

---

# 👟 Inventory Management

The inventory system is primarily designed around Shop in V1.

A sneaker record can contain information such as:

- Brand
- Model
- Size
- Cost price
- Selling price
- Quantity

The system tracks available stock and updates inventory when sales occur.

Inventory management also includes:

- Adding Shop
- Viewing inventory
- Monitoring quantities
- Low-stock detection
- Restocking
- Stock-related notifications

---

# 💰 Sales Management

Authorized users can record sneaker sales through the application.

A sale records information associated with:

- Product
- Quantity
- Selling value
- Profit
- User who recorded the sale
- Sale date

When a sale occurs, the application can:

1. Record the sale.
2. Reduce inventory quantity.
3. Calculate the transaction profit.
4. Update dashboard statistics.
5. Generate appropriate notifications.
6. Trigger low-stock logic when applicable.

This creates a direct relationship between inventory and sales activity.

---

# 📈 Sales Analytics and History

MikeShop V1 provides a dedicated Sales History/Analytics interface.

The system supports:

- Historical sales records
- Date-based filtering
- Sales pagination
- Sales analysis
- Best-selling information
- Revenue tracking
- Profit tracking
- Sales export
- Receipt generation

The sales history system is intended to provide both operational records and management insight.

---

# 💸 Expense Management and Accountability

Expense management was specifically designed in V1 to make business expenses accountable.

Only:

- Owner
- Admin

are permitted to add expenses.

An expense is associated with the user who created it.

This makes it possible to determine:

> **Who entered this expense?**

rather than simply recording an unexplained expense amount.

Expense information includes concepts such as:

- Expense title
- Amount
- Date
- User/account responsible for recording it

This provides an accountability layer for business spending.

---

# 📦 Restocking

MikeShop V1 includes a dedicated restocking mechanism.

Restocking allows inventory quantities to be increased while maintaining information associated with the restock.

Restock records can contain:

- Sneaker
- Quantity
- Cost
- Supplier
- Date

The restocking system is connected to inventory management so that stock replenishment can be reflected in the inventory.

---

# 👨‍💼 Staff Management

The Owner has access to staff-management functions.

The V1 system supports functionality such as:

- Creating staff accounts
- Managing staff
- Assigning roles
- Updating roles
- Resetting passwords
- Removing staff

The role system prevents ordinary staff members from performing owner-only administrative operations.

The dashboard and staff-management interfaces use pagination/scrollable presentation where appropriate rather than imposing a hard business limit such as 30 staff members.

The exact practical capacity is therefore determined more by the database, hosting environment, interface design, and future scaling architecture than by an arbitrary 30-user limit.

---

# 🔔 Notifications

MikeShop V1 includes a notification system.

Notifications are used for important events such as:

- Sales
- Low-stock conditions
- Other relevant application activity

The application uses Flask-SocketIO to support real-time communication between the server and browser.

This allows the dashboard to provide more immediate feedback without requiring every update to be manually refreshed.

---

# 📝 Audit Logging

The application includes an audit-log model for recording important user activity.

Audit logging is intended to provide traceability for administrative and operational actions.

This is especially important for a multi-user business application because it helps answer questions such as:

- Who performed an action?
- What action occurred?
- When did it occur?

Audit logging provides an important foundation for future security and accountability improvements.

---

# 🧾 Receipts and PDF Export

MikeShop V1 supports sales documentation.

The application can generate sales receipts and PDF-related sales output.

The PDF functionality is implemented using:

**ReportLab**

Sales records can also be exported for external use and reporting.

---

# 🔐 Authentication and Security

MikeShop V1 includes several security-oriented mechanisms.

## Authentication

Users must authenticate before accessing protected application functionality.

---

## Role-Based Access Control

Sensitive operations are restricted based on role.

Examples include:

- Staff management
- Expense creation
- Inventory administration
- Administrative operations

---

## Password Security

Passwords are not intended to be stored as plain text.

The application uses Werkzeug password hashing utilities for password handling.

---

## Environment Variables

Sensitive deployment configuration such as the initial owner password is supplied through environment variables.

For example:

```text
OWNER_PASSWORD#   M i k e S h o p - v 1  
 