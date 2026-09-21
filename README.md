# סורק גראהם — S&P 500

סריקה יומית אוטומטית של כל מניות מדד ה-S&P 500 מול שבעת הקריטריונים של בנג'מין גראהם
למשקיע המגן, מתוך "המשקיע הנבון" (פרק 14).

**הדף החי:** לאחר הפעלת GitHub Pages, הכתובת תהיה
`https://<שם-המשתמש>.github.io/<שם-המאגר>/`

## איך זה עובד

| קובץ | תפקיד |
|---|---|
| `graham_screener.py` | מושך נתונים מ-Yahoo Finance ומחשב את שבעת הקריטריונים לכל מניה |
| `build_page.py` | הופך את `results.csv` לדף אינטרנט (`docs/index.html`) |
| `.github/workflows/daily.yml` | מריץ את שניהם אוטומטית בכל יום מסחר ב-21:30 UTC |

הריצה האוטומטית מתבצעת על שרתי GitHub, בלי תלות במחשב כלשהו.
אפשר גם להריץ ידנית מלשונית **Actions** → *Daily Graham screen* → *Run workflow*.

## הרצה מקומית

```bash
pip install -r requirements.txt
python graham_screener.py --universe sp500 --out results.csv
python build_page.py --in results.csv --out docs/index.html
```

לבדיקה מהירה על מניות בודדות:

```bash
python graham_screener.py --tickers KO,JNJ,PG,XOM,IBM
```

## הגדרה חד-פעמית של GitHub Pages

Settings → Pages → Source: `Deploy from a branch` → Branch: `main`, תיקייה: `/docs` → Save.

## הבהרה

זהו כלי סינון טכני-כמותי בלבד. הוא אינו ייעוץ השקעות ואינו המלצה לקנות או למכור נייר ערך
כלשהו. הנתונים מגיעים ממקור חינמי ועשויים להיות חלקיים או שגויים.
