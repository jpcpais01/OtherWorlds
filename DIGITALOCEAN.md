# DigitalOcean setup, step by step

Steps marked **💰** cost money. A GPU Droplet bills **every hour it exists,
even when powered off**. Only **Destroy** stops billing.

Why DigitalOcean over RunPod: a full machine with a public IP, so WebRTC can
connect directly over UDP (no relay), and a European region (Amsterdam) for
a lower ping from Europe.

## 1. Account (free)

Sign up at <https://www.digitalocean.com> and add a payment method. New
accounts sometimes have to request access to GPU Droplets; if the GPU page
asks, submit the request (approval can take a while).

## 2. Choose the machine (free until you click Create)

**Create → GPU Droplets**, then:

- **Region:** the closest one to you that offers GPUs (Amsterdam for Europe).
- **GPU:** 1x **H100**. If it's unavailable, 1x **L40S** or **RTX 6000 Ada**
  also run the SDXL engine, a bit slower and cheaper.
- **Image:** **AI/ML Ready** (Ubuntu with NVIDIA drivers).
- **Authentication:** add an SSH key if it's required. On your PC, run
  `ssh-keygen -t ed25519` in a terminal (Windows: PowerShell), press Enter
  through the questions, then paste the contents of
  `~/.ssh/id_ed25519.pub` (Windows: `C:\Users\<you>\.ssh\id_ed25519.pub`).
- No extra volumes, no backups.

## 3. 💰 Create

Click **Create GPU Droplet**. Hourly billing starts now. Wait until it shows
an IP address.

## 4. Install (about 5 minutes)

Open a terminal on the Droplet: either the **Console** button on its page,
or from your PC: `ssh root@<droplet-ip>`. Then:

```bash
git clone -b claude/project-thread-izhm9c https://github.com/jpcpais01/OtherWorlds
cd OtherWorlds
bash scripts/vm_setup.sh
```

It ends with `VM setup done`.

## 5. Start

```bash
bash scripts/vm_run.sh
```

It prints `=== Open on your phone: https://….trycloudflare.com ===` (a free
HTTPS tunnel; phones only allow the camera on HTTPS pages). The first start
downloads ~12 GB of model weights; wait for `engine ready: sdxl-turbo`.

Open the link on your phone, set **Link → WebRTC**, and tap **Start**. Here
WebRTC connects directly, so it should beat WebSocket; compare both.

The link changes each time you run `vm_run.sh`.

## 6. 💰 Stop billing when done

- **Power Off does not stop billing.** You must **Destroy** the Droplet:
  Droplet page → **Destroy** → **Destroy this Droplet**, and confirm.
- Check **Volumes** and **Snapshots** (left menu) are empty.
- **Billing** should show no running resources.
