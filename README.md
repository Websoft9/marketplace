# Marketplace Operation

A Websoft9 automated operations project for mainstream cloud marketplaces. It maintains listed products more efficiently so users get highly available, usable products and versions.

## Websoft9 at Major Cloud

- **[Aliyun](https://market.aliyun.com/store/228399/index.html) / [Alibaba Cloud](https://marketplace.alibabacloud.com/store/2116499.html)**
- **[Huaweiyun](https://marketplace.huaweicloud.com/seller/b8f57c06a0f7e39f4e206560e71afd76) / [Huawei Cloud](https://marketplace.huaweicloud.com/intl/seller/791bea8ce08849cf9ae4ec61c27e8435)**
- **[Azure](https://azuremarketplace.microsoft.com/en-us/marketplace/apps?search=websoft9inc)**
- **[AWS](https://aws.amazon.com/marketplace/seller-profile?id=c639a579-182c-4d30-8578-4d4d89fba658)**
- **[Google Cloud](https://console.cloud.google.com/marketplace/browse?filter=partner:Websoft9)**

## AI Collaboration

- Repository guidelines and constraints: [`AGENTS.md`](AGENTS.md)
- Project overview and conventions: [`specs/`](specs/README.md)
- Repository-specific skills: [`skills/`](skills/README.md)
- VS Code MCP configuration: `.vscode/mcp.json` (GitHub MCP uses GitHub OAuth; Playwright MCP requires Node.js/npx)

## For Business

- **Operations dashboard** — use the [Marketplace dashboard](https://websoft9.github.io/marketplace) to review application listings and marketplace data.
- **Automated checks** — workflows check product versions, content, and compliance to support manual review and optimization.
- **AWS listing content** — application facts are staged in `data/.work/catalog.json`; the repository maintains templates, vendor statements, and AWS listing rules.
- **Review and plan updates** — use `mk offer build --app <key> --cloud aws` for an offline preview, then `mk offer plan` to prepare a live, revision-bound update plan.
- **Approval-controlled submission** — manually run **Marketplace Offer Apply** to generate plans and submit updates after approval in `aws-marketplace-production`. See the [update workflow](specs/marketplace-offer-update-workflow.md) and [Odoo first-publication guide](specs/odoo-first-publication.md).
- **Legacy workflow** — the Issue-driven product-file update workflow is paused.

## For Developer

- `make help` — list development commands.
- `make cli` — enter business commands in an isolated `.venv` (`mk help`).