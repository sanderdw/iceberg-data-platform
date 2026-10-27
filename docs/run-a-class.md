# Run a class

In this tutorial you prepare the platform for a class or workshop: every participant works from their own laptop, in a team of a fictional company, on one shared installation. You install the platform on a Kubernetes cluster, let an agent create the company and its accounts, and print a sign-in slip for each participant. Plan an hour before the class, most of it waiting for the cluster and its certificates.

You need:

- a Kubernetes cluster with an ingress controller and cert-manager, and a domain whose DNS you control; see [what you need](install-kubernetes.md#what-you-need)
- a coding agent, such as Claude Code, Codex or GitHub Copilot, on a computer where `kubectl` reaches the cluster
- the number of participants, at most 48 per installation

## Why Kubernetes and not a laptop

For a class, install on Kubernetes. Don't share a Compose installation on your laptop through the [`quick-share` skill](agent-skills.md), except for a short demo:

- **Your own machine is exposed.** With Compose, the user portal mounts the Docker socket, which gives host-level access. Quick-share puts that portal on the internet, on the laptop you also use for everything else. The Kubernetes chart has no Docker socket: notebooks run as pods in their own namespace, held to the restricted Pod Security standard and NetworkPolicies.
- **Anyone with the address reaches the sign-in page.** Cloudflare offers no access control for quick tunnels, and their addresses are random and public.
- **Quick tunnels are for testing only.** Cloudflare gives no uptime guarantee and a limit of 200 concurrent requests. The address changes whenever the tunnel restarts, so every slip you handed out stops working.
- **Not everything is shared.** dbt and Ingestion are unreachable through the tunnels, and sign-in on `localhost` stops working until you end the session.

A Kubernetes installation has stable HTTPS addresses with trusted certificates for every portal, and keeps the Keycloak administration console and the Polaris management API inside the cluster. See the [security model on Kubernetes](security.md#on-kubernetes).

## 1. Install the platform

Start your coding agent in a new, empty folder, with `kubectl` pointing at the cluster, and give it this prompt, with your class size:

```text
Install the Iceberg Data Platform on my Kubernetes cluster for a class of <number> participants.
Download the installation archive of the latest release from
https://github.com/sanderdw/iceberg-data-platform/releases/latest, unpack it here,
and follow its kubernetes-install skill in .agents/skills/kubernetes-install/SKILL.md.
Set notebooks.max to the class size, and ask me which extensions the class needs.
```

The archive holds the [`kubernetes-install` skill](agent-skills.md) and the other agent skills, so the agent works from the release you install. The skill checks the cluster, asks for the domain, certificates and PostgreSQL, and runs each command after you approve it. It ends by checking every host from outside and printing where to sign in.

The prompt covers the two choices that matter for a class:

- **Notebooks.** Each participant who opens a notebook runs one pod, and the chart allows 8 at once. Give the cluster room for one pod per participant, each requesting 512 MiB and using up to 2 GiB, or let its node pool scale out.
- **Extensions.** Install only the ones the class uses. Conversational BI answers every participant's question on your LLM key, at your cost.

## 2. Sign in as the platform administrator

Read the one-time password from the platform Secret:

```bash
kubectl -n iceberg get secret iceberg-platform -o jsonpath='{.data.PLATFORM_ADMIN_PASSWORD}' | base64 -d; echo
```

Open `https://admin.<domain>`, sign in as `platform-admin` and choose a new password. The overview shows no teams yet.

## 3. Create the company

Stay in the same folder and give your agent this prompt, with your domain:

```text
Set up a demo company for my class on https://admin.<domain>,
with the demo-company skill in .agents/skills/demo-company/SKILL.md.
```

If the administration MCP server isn't registered yet, the agent tells you how; start a new session afterwards so its tools load, and give the prompt again. Sign in as `platform-admin` when the browser opens. The skill asks how many participants there are and which company to create (Energy, Webshop or Retail), then creates:

- teams of about four people, each with a production and a development database
- one account per participant: one Administrator per team, everyone else Read & write
- read access to every team for you, so you can follow the class in the user portal

It ends with a summary of every username and one-time password, in `demo-company-<domain>-credentials.md`, and with `demo-company-<domain>-slips.html`: one slip per participant with the user portal address as a QR code, their team, username and one-time password.

## 4. Check from another device

Before the class, check the address the participants will use, without spending one of their accounts:

1. On a phone or another computer, scan the QR code on a slip, or open `https://users.<domain>`.
2. Sign in as `platform-admin`.

You see the company's teams under **Active team**, each with its two databases, and the **Getting started** page. The participants see the same, for their own team only.

## 5. Teach the class

1. Open the slips in a browser, print them on A4 and cut them apart. Hand one to each participant.
2. Participants sign in, choose a password and follow **Getting started** in the user portal.
3. Start them on **Notebooks** with example **01**, which creates the data that the later examples read. The [first steps](first-steps.md) tutorial follows the same path from step 5.
4. Follow along in the user portal as `platform-admin`: switch the **Active team** to look at each team's databases, read-only.

Team Administrators can create more databases and share data with other teams, so a class can build a data product in one team and use it in another.

## 6. After the class

1. Delete the credentials file and the slips: they hold live passwords.
2. Remove the platform when you no longer need it. Uninstalling keeps the data; deleting the namespaces removes it for good. Uninstall the extensions you installed first; see [uninstall](install-kubernetes.md#uninstall):

    ```bash
    helm uninstall iceberg -n iceberg
    kubectl delete namespace iceberg iceberg-notebooks
    ```

3. Delete the cluster and its load balancer at your cloud provider; they cost money while they run.

## What you did

You installed the platform where every participant can reach it safely, created a fictional company with one account per participant, and handed out sign-ins that work for the whole class. Every participant worked in their own team, with their own grants, on one installation.

If more participants join, run the `demo-company` skill again with the new total: it adds only what is missing. For a new class, start from a fresh installation.
