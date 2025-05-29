# Welcome to BeeHive-Cogs
![GitHub Issues or Pull Requests](https://img.shields.io/github/issues/BeeHiveSafety/BeeHiveCogs)

> Enjoy these cogs? Please consider [supporting them financially](https://donate.stripe.com/5kAeVYenp9Yh9GgcMP) with a donation of any amount to help keep them maintained.

## About this repo
Red is a free, self-hostable, open-source Discord bot that can be used in growing Discord communities to help personalize and modularize server management, moderation, games, and more. 

This repository contains cogs that can help equip your Red instance with advanced features that safeguard your community, and improve your member's server experience.

## Adding the repo
Before you can install our cogs, you need to add our repo to your instance so it can find, our cogs by name. To do so, run the following command.

```
[p]repo add BeeHiveSafety https://github.com/BeeHiveSafety/BeeHiveCogs
```

When you run this command, Red may give you a warning about installing third party cogs. If you're presented with this, you'll need to respond in chat with "I agree". 

We'll provide a, relatively same-in-spirit disclaimer below.

>[!CAUTION]
>**Installing third-party cogs can increase resource utilization, create security risks, reduce system stability, and otherwise severely degrade usability of your bot, especially in resource-restricted environments.** 
>
>**Only install cogs from sources that you trust. The creator of Red and its community have no responsibility for any potential damage that the content of 3rd party repositories might cause.** 


## Public cogs
Our public cogs are the cogs we make that do, assorted things. Maybe you'll find them useful - or maybe not. 

### [weatherpro](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/weatherpro)

Access detailed weather information, forecasts, and historical data for any location in the United States using ZIP codes. Perfect for planning events, travel, or just staying informed about the weather in your area. `[p]weather`, `[p]weatherset`.


```
[p]cog install BeeHiveSafety weatherpro
```
```
[p]load weatherpro
```

### [adaptiveslowmode](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/adaptiveslowmode)

Dynamically adjust Discord channel slowmode in 1-second increments based on real-time chat activity, keeping conversations readable and moderatable without constant manual intervention. AdaptiveSlowmode automatically increases or decreases slowmode to target a configurable messages-per-minute rate, and provides interactive log messages with buttons for manual adjustment.

**Key features**
- Automatically tunes slowmode for each channel based on recent message activity.
- Set minimum/maximum slowmode, target messages per minute, and which channels to monitor.
- Interactive log messages (with buttons) allow manual slowmode adjustment from Discord.
- Survey command to calibrate settings based on 5 minutes of real activity.
- Logging channel support for activity and adjustment reports.

```
[p]cog install BeeHiveSafety adaptiveslowmode
```
```
[p]load adaptiveslowmode
```

### [ping](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/ping)

A nice, functional ping-and-speedtest cog that shows your host latency, transit latency, download speed, and upload speed in a neat, orderly, no-frills embed. If your bot is hosted on a poor quality connection, includes a special offer when detected. `[p]ping`.

```
[p]cog install BeeHiveSafety ping
```
```
[p]load ping
```

### [names](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/names)

Help manage unruly, unsightly, and otherwise annoying nicknames/screennames in your server. Purify and normalize visually obnoxious names manually, or enable automatic cleanups to keep your server tidy. `[p]nickname`.

```
[p]cog install BeeHiveSafety names
```
```
[p]load names
```

### [linksafety](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/linksafety)

Passively detect and remove known malicious websites sent in your server's chats. `[p]linksafety`.

```
[p]cog install BeeHiveSafety linksafety
```
```
[p]load linksafety
```

### [skysearch](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/skysearch)

Interactive features to let you explore and search for aircraft by their registrations, squawks, ICAO 24-bit addresses, and more, as well as fetch information about airports like locations, photos, forecasts, and more. `[p]aircraft`, `[p]airport`.

```
[p]cog install BeeHiveSafety skysearch
```
```
[p]load skysearch
```

### [disclaimers](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/disclaimers)

Set up and manage pre-defined disclaimers that attach to users of particular significance, like lawyers, financial advisors, or other professions where a disclaimer may be warranted as a responsible disclosure. `[p]disclaimers`.

```
[p]cog install BeeHiveSafety disclaimers
```
```
[p]load disclaimers
```

### [serverinfo](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/serverinfo)

Provides detailed information about your Discord server, including member statistics, channel counts, role information, and more. Useful to keep track of various server metrics and check if the server is configured, relatively, correctly. `[p]serverinfo`.

```
[p]cog install BeeHiveSafety serverinfo
```
```
[p]load serverinfo
```

### [invites](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/invites)

Manage and track invite links for your Discord server. This cog allows you to see who invited whom, track the number of uses for each invite link, and generate new invite links with specific settings. Useful for community growth and moderation. `[p]invites`.

```
[p]cog install BeeHiveSafety invites
```
```
[p]load invites
```

## Brand cogs
Brand cogs are cogs we make that are intended to integrate other third party services with your Red instance. Red is a powerful tool when correctly equipped, and we hope these cogs help extend your bot's capabilities in your own community.

>[!TIP]
>Unless otherwise specified, brand cogs are not authored, audited, or endorsed by the brands and tools that they interact with.
>These are made open-effort and open-source to extend the functionality of Red-DiscordBot, not to imbibe an endorsement of any one specific brand.
>If you choose to use these in potentially sensitive environments, this is the disclaimer that indicates you do so at your own risk and liability.

### [abuseipdb](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/abuseipdb)

Integrate the AbuseIPDB API with your Red-DiscordBot to check and report IP addresses for abusive activity. This cog allows you to query the reputation of an IP address and report malicious IPs directly from your Discord server. `[p]abuseipdb`.

```
[p]cog install BeeHiveSafety abuseipdb
```
```
[p]load abuseipdb
```

### [cloudflare](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/cloudflare)

Integrate Cloudflare's URL Scanner and Cloudflare Intel APIs with your Red-DiscordBot.

**Key features**
- Scan URLs for threats using Cloudflare's URL Scanner, both manually and automatically.
- Search and view historical URL scan results.
- Enable automatic scanning of all posted URLs in your server, with optional logging to a channel.
- Query Cloudflare Intel for domain, IP, ASN, and WHOIS intelligence directly from Discord.
- Download detailed reports for domains and WHOIS lookups.

> **Note:** You must set your Cloudflare API credentials using Red's shared API tokens for this cog to function.

### [virustotal](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/virustotal)

Utilize the VirusTotal API with a free API key to scan and analyze files for potential threats and malicious content. `[p]virustotal`.

```
[p]cog install BeeHiveSafety virustotal
```
```
[p]load virustotal
```

### [urlscan](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/urlscan)

Use the URLScan.io API with a `free` URLScan API Key to evaluate URLs for safety and security. Enable `[p]urlscan autoscan` to automatically monitor and protect your chat from potentially harmful links. `[p]urlscan`.

```
[p]cog install BeeHiveSafety urlscan
```
```
[p]load urlscan
```
```
[p]set api urlscan api_key YOURAPIKEYHERE
```

### [ransomwaredotlive](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/ransomwaredotlive)

Integrate the Ransomware.live API with your Red-DiscordBot to monitor and receive updates on the latest ransomware activities as well as query information about recent and historical ransomware attacks.`[p]ransomware`.

```
[p]cog install BeeHiveSafety ransomwaredotlive
```
```
[p]load ransomwaredotlive
```

### [automod](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/automod)

Utilize OpenAI's frontier moderation models to keep chat clean in your server. `[p]automod`

```
[p]cog install BeeHiveSafety automod
```
```
[p]load automod
```
```
[p]set api openai api_key YOURAPIKEYHERE
```

### [shazam](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/shazam)

Automatically identify songs shared in chat as files.

```
[p]cog install BeeHiveSafety shazam
```
```
[p]load shazam
```

### [transcriber](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/transcriber)

Automatically transcribe and moderate voice notes and other audio sent in your server using OpenAI. `[p]transcriber`

```
[p]cog install BeeHiveSafety transcriber
```
```
[p]load transcriber
```
```
[p]set api openai api_key YOURAPIKEYHERE
```

### [triageanalysis](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/triageanalysis)

Integrate the [Tria.ge](https://tria.ge/) malware analysis sandbox with your Red-DiscordBot. Automatically or manually scan suspicious files, enforce punishments, and get rich analysis reports in Discord. `[p]triage`

>[!TIP]
>Visit [tria.ge/account](https://tria.ge/account) to generate an API key. 

```
[p]cog install BeeHiveSafety triageanalysis
```
```
[p]load triageanalysis
```
```
[p]set api triage api_key YOURAPIKEYHERE
```

## Proof-of-concept cogs

These are cogs that we make to showcase the usefulness/application of one or two specific bits of tech functionality. Are they meant for your bot? Absolutely not. Is there a chance you might learn something cool from them code-wise? Absolutely.

### [schoolworkai](https://github.com/BeeHiveSafety/BeeHiveCogs/tree/main/schoolworkai)

A full-featured, privacy-focused AI homework assistant for Discord. SchoolworkAI provides `/ask`, `/answer`, `/explain`, and `/outline` commands for students, with onboarding, billing, invite rewards, and usage/rating stats.





