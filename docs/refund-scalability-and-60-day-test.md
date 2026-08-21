# Refund scalability and 60-day test

Date: 2026-08-21

## Verdict

Refund has a large potential market, but it is not yet a scalable product.

The market risk is not whether people lose money in failed purchases. The main
risks are whether Lians can earn enough trust to receive a real case, complete
the recovery faster than a person or general AI, and make human work fall as
case volume rises.

The honest 60-day base target is:

> **375 signups, 150 real cases, and 34 confirmed refunds.**

This is a planning scenario, not a forecast. Direct Lians evidence is still
zero. The goal of the test is to learn whether the funnel and operating model
can become self-serve.

## Current pressure test

| Requirement | Score | Reason |
|---|---:|---|
| Market depth | 9/10 | Failed purchases, missing orders, duplicate charges, and late refunds are common. |
| Outcome clarity | 10/10 | Money confirmed back is a finished, measurable result. |
| Broad potential use | 9/10 | Most online shoppers can understand the problem without belonging to a niche profession. |
| Technical scalability today | 6/10 | Email, approval, reminders, and case state can be automated, but merchant systems vary. |
| Operational scalability today | 3/10 | Lians has no merchant playbooks or measured no-touch resolution rate. |
| Future margins | 4/10 | Low-value refunds cannot support much human work. |
| Repeat use | 6/10 | Problems recur, but not daily for every person. |
| Product-led distribution | 5/10 | Recovered money is compelling, but the result is private and can take days. |
| Trust and safety readiness | 4/10 | Claims, consent, identity, sensitive evidence, and fraud need strict controls. |
| Direct Lians proof | 0/10 | No real person has received money back through Lians. |

The current scalability verdict is **5/10**. It can become 8/10 only after the
live test proves repeatable merchant patterns, low human time, safe approvals,
and successful recoveries.

## What the current market proves

- U.S. e-commerce sales reached $1.234 trillion in 2025. This establishes a
  very large transaction base, not a Lians revenue forecast. See the [U.S.
  Census Bureau 2025 e-commerce report](https://www2.census.gov/retail/releases/historical/ecomm/25q4.pdf).
- The National Retail Federation estimated $849.9 billion in merchandise
  returns during 2025 and a 19.3 percent return rate for online sales. It also
  reported that 71 percent of consumers are less likely to shop with a retailer
  after a poor return experience. See the [2025 NRF returns
  report](https://nrf.com/media-center/press-releases/consumers-expected-to-return-nearly-850-billion-in-merchandise-in-2025).
- Pew found that 36 percent of U.S. adults have ever bought an item online that
  did not arrive or was counterfeit and was not refunded. The word "ever" is
  important. This is evidence of a widespread problem, not an annual market
  size. See [Pew Research Center](https://www.pewresearch.org/internet/2025/07/31/online-scams-and-attacks-in-america-today/).
- YouGov reported that 37 percent of Americans shop online at least weekly. See
  the [2025 U.S. retail and delivery
  report](https://yougov.com/en-us/reports/52101-us-retail-online-delivery-report-2025).
- Rocket Money reports more than 10 million members and more than $2.5 billion
  saved. This adjacent result shows that a broad consumer product can grow by
  taking unwanted money problems off the user's plate. It does not prove demand
  for Lians. See [Rocket Money](https://www.rocketmoney.com/about).
- Lumo currently pursues refunds for users in the UK, requires approval, and
  charges 10 percent only after recovery. This validates the category and shows
  that the Lians idea is not unique by itself. See [Lumo](https://lumoapp.org/).

## Potential users

Potential users must be separated into three different numbers.

### Category audience

Tens of millions of U.S. adults have experienced the underlying problem. The
Pew result and online shopping frequency support that conclusion. They do not
tell us how many cases are current, eligible, recoverable, or valuable enough
for Lians.

### Plausible long-term product audience

A reasonable research scenario is **5 million to 20 million U.S. consumers** if
Lians expands from merchant refunds into a trusted money-recovery product. This
is an inference, not a measured total addressable market. The range is anchored
by the broad problem evidence and Rocket Money's reported 10 million adjacent
users.

### Obtainable users in the first 60 days

The first 60 days are constrained by attention, case eligibility, trust, and
operator capacity. A realistic range is **75 to 1,500 signups**. A breakout
campaign could exceed that, but the operation cannot safely serve the volume
until the product automates most supported cases.

## 60-day growth model

The model uses explicit assumptions so real data can replace them:

- 15 percent of visitors create a free account;
- 40 percent of signups submit a real case;
- 60 percent of submitted cases fit the first scope;
- 75 percent of supported users approve the first action; and
- 50 percent of approved cases produce a confirmed refund by day 60.

That means about **1.35 percent of visitors become confirmed refunds**.

| Scenario | Visitors | Signups | Real cases | Supported | Approved | Confirmed refunds |
|---|---:|---:|---:|---:|---:|---:|
| Conservative | 500 | 75 | 30 | 18 | 14 | 7 |
| Base | 2,500 | 375 | 150 | 90 | 68 | 34 |
| Strong | 10,000 | 1,500 | 600 | 360 | 270 | 135 |
| Breakout | 50,000 | 7,500 | 3,000 | 1,800 | 1,350 | 675 |

The base case is the working target. The strong case becomes credible only
after real conversion data and operating time beat the assumptions.

## Why this cannot copy Bolt's 60-day curve yet

Bolt reports growing from zero to $20 million in annual recurring revenue in
60 days with a team of 15. See [Bolt's company account of the growth
period](https://www.linkedin.com/posts/boltdotnew_how-did-we-take-a-lean-team-of-15-and-scale-activity-7351662056787374080-gyKZ).

Refund starts with a harder growth loop:

1. Bolt can create a visible result in minutes. A refund can take days.
2. A built app is naturally public and shareable. A refund is private.
3. Trying Bolt requires a prompt. Trying Refund requires trust, a real problem,
   and sensitive evidence.
4. Bolt's software can serve the next user at low marginal cost. Refund starts
   with merchant exceptions and manual case work.
5. Almost any curious visitor can try building. Only visitors with a current,
   supported loss can activate Refund.

The lesson to copy is not the number. It is the single outcome, immediate
demonstration, short path to value, and product loop that makes the next result
cheaper to produce.

## Operator capacity test

At fifteen human minutes for every supported case:

| Scenario | Supported cases | Human hours |
|---|---:|---:|
| Conservative | 18 | 4.5 |
| Base | 90 | 22.5 |
| Strong | 360 | 90 |
| Breakout | 1,800 | 450 |

At forty-five minutes per case, those loads become 13.5, 67.5, 270, and 1,350
hours. A viral launch before automation would create a service backlog, not a
scalable product.

The required scale gate is:

- at least 80 percent of supported cases need no operator action after user
  approval;
- median total operator time must fall below five minutes per supported case;
- at least 70 percent of supported cases must fit a reusable merchant pattern;
  and
- case completion time must keep improving as volume rises.

## Future unit economics

Free access is the correct demand test, but free users still create operating
cost. A future 10 percent success fee produces the following expected revenue
per supported case under the planning assumptions of 75 percent approval and 50
percent confirmed recovery:

| Average successful refund | Expected revenue per supported case |
|---:|---:|
| $60 | $2.25 |
| $120 | $4.50 |
| $250 | $9.38 |

Fifteen minutes of human work at $30 per hour costs $7.50 before infrastructure,
support, payment fees, fraud, or acquisition. The model is weak for small cases.

The first cohort can accept cases worth at least $25 to learn. A scalable
cohort should favor recoveries worth at least $150 until median operator time is
below five minutes. Lians must later test whether users prefer a success fee,
membership, or another model. Pricing is not part of the first proof gate.

## Exact 60-day test

### Days 1 to 14: prove the outcome

- complete the existing twenty-person free cohort;
- recover at least eight refunds and more than $1,000;
- compare every supported case with the shortest ordinary ChatGPT or Claude
  alternative;
- record user time, operator time, refund amount, and resolution pattern; and
- stop unsupported or suspicious claims before any external message.

### Days 15 to 30: find the repeatable wedge

- cluster completed cases by merchant and failure type;
- select the two patterns with the highest success and lowest human time;
- automate evidence extraction, approval, reply classification, and deadlines
  for only those patterns;
- publish the first privacy-safe result pages with explicit user consent; and
- ask every successful user for one person with a current eligible case.

### Days 31 to 45: test distribution

- launch merchant-specific and problem-specific pages based on completed cases;
- publish short demonstrations showing forward, approve, and money returned;
- give each successful user a referral link tied to a real submitted case;
- recruit consumer creators for result-based demonstrations; and
- measure referral cases and search cases separately from direct outreach.

### Days 46 to 60: test scale

- target the 375-signup base scenario;
- process the last fifty supported cases through the repeated playbooks;
- measure no-touch rate and operator time by merchant pattern;
- verify every public recovery claim against a completed record; and
- choose scale, narrow, or stop using the gates below.

## Day-60 decision gates

### Scale

Scale the winning case patterns only if:

- at least 34 confirmed refunds land from at least 90 supported cases;
- at least $5,000 is confirmed recovered;
- median user time is below three minutes;
- median operator time is below five minutes for the last fifty supported
  cases;
- at least 80 percent of those cases need no operator action after approval;
- at least 20 percent of successful users produce a qualified referral;
- at least 10 percent submit a second valid case within 30 days;
- no false claim, unauthorized action, or material data incident occurs; and
- the product beats the general AI baseline on completed outcome or user effort.

### Narrow

Narrow to one merchant or one failure type if recoveries succeed but exceptions,
human time, or trust prevent broad automation.

### Stop

Stop Refund if:

- fewer than eight confirmed refunds land in the initial twenty-person cohort;
- users will not submit real evidence or approve the first action;
- a general AI email achieves the same completion rate with the same user work;
- operator time does not decline after repeated cases;
- fraud and verification controls make the simple experience impossible; or
- referrals and repeat cases remain near zero after successful recoveries.

## Cohort record

Track only anonymized aggregate data in GitHub. Raw receipts, names, addresses,
emails, payment details, merchant messages, and account evidence must never be
committed.

For each case, record privately:

- anonymous case ID;
- acquisition source;
- merchant pattern and failure type;
- amount requested;
- supported or rejected reason;
- approval timestamp;
- first action timestamp;
- confirmed result and amount recovered;
- user minutes and operator minutes;
- general AI baseline result;
- referral submitted; and
- second valid case submitted.

## What would change the verdict

The idea moves from 5/10 to 8/10 only when real results prove declining human
work, safe external action, meaningful referrals, repeated use, and positive
future economics. No research estimate can raise it to 10/10. Only user
behavior can.
