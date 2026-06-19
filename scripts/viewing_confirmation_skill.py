#!/usr/bin/env python3
"""
VIEWING CONFIRMATION SKILL v2
Consolidates confirmed property viewers and sends personalized confirmation messages

Features:
- Extract confirmed viewers from chat history
- Generate coordinator briefs
- Send personalized confirmation messages to each viewer
- Track confirmation status

Usage:
  # Send confirmations to all viewers
  python3 viewing_confirmation_skill.py --property "Caspian" --coordinator "Don Chuang" --phone "+65 8526 0116" --address "Block 60, #16-44" --send

  # Generate brief only (no send)
  python3 viewing_confirmation_skill.py --property "Caspian" --coordinator "Don" --phone "+65 8526 0116" --brief

  # Export as JSON
  python3 viewing_confirmation_skill.py --property "Caspian" --output json
"""

import sqlite3
import json
import sys
import argparse
import requests
from datetime import datetime, timedelta
import re

class ViewingConfirmationSkill:
    def __init__(self, db_path=None):
        self.db_path = db_path or "/Users/winfredquek/whatsapp-mcp/whatsapp-bridge/store/messages.db"
        self.conn = sqlite3.connect(self.db_path)
        self.cursor = self.conn.cursor()
        self.wa_api = "http://127.0.0.1:8080/api/send"

    def get_confirmed_viewers(self, property_name, days_back=7):
        """Extract all confirmed viewers for a property from chat history"""
        query = f"""
        SELECT DISTINCT chat_jid,
               MAX(CASE WHEN is_from_me=0 THEN content ELSE NULL END) as last_msg
        FROM messages
        WHERE (content LIKE '%{property_name}%' OR content LIKE '{property_name.lower()}%')
          AND timestamp > datetime('now', '-{days_back} days')
        GROUP BY chat_jid
        ORDER BY chat_jid
        """

        self.cursor.execute(query)
        return self.cursor.fetchall()

    def extract_prospect_profile(self, jid):
        """Extract profile information from prospect's messages"""
        self.cursor.execute(f"""
        SELECT content, is_from_me, timestamp
        FROM messages
        WHERE chat_jid = '{jid}'
        ORDER BY timestamp DESC
        LIMIT 200
        """)

        msgs = self.cursor.fetchall()
        profile = {
            'jid': jid,
            'name': 'Unknown',
            'phone': None,
            'nationality': None,
            'gender': None,
            'age': None,
            'pass_type': None,
            'pax': None,
            'move_in': None,
            'budget': None,
            'confirmation': None,
            'viewing_time': None,
        }

        for content, is_from_me, ts in msgs:
            if not content:
                continue

            content_lower = content.lower()

            # Extract name
            if 'name:' in content_lower and is_from_me == 0 and profile['name'] == 'Unknown':
                parts = content.split('\n')
                for p in parts:
                    if 'name:' in p.lower():
                        profile['name'] = p.split(':')[1].strip() if ':' in p else 'Unknown'
                        break

            # Extract confirmation keywords
            if any(word in content_lower for word in ['yes', 'confirm', 'ok', 'sure', 'can', 'works']):
                if is_from_me == 0:
                    profile['confirmation'] = 'Confirmed'

            # Extract time preferences
            if 'pm' in content_lower or 'am' in content_lower:
                if is_from_me == 0:
                    time_match = re.search(r'(\d{1,2}):?(\d{2})?\s*(?:pm|am)', content_lower)
                    if time_match:
                        profile['viewing_time'] = f"{time_match.group(0)}"

            # Extract other fields
            if 'gender:' in content_lower and is_from_me == 0:
                profile['gender'] = 'Male' if 'male' in content_lower else 'Female'

            if 'nationality:' in content_lower and is_from_me == 0:
                if 'indian' in content_lower:
                    profile['nationality'] = 'Indian'
                elif 'chinese' in content_lower:
                    profile['nationality'] = 'Chinese'
                elif 'singaporean' in content_lower or 's\'porean' in content_lower:
                    profile['nationality'] = 'Singaporean'

            if 'age:' in content_lower and is_from_me == 0:
                age_match = re.search(r'age:\s*(\d+)', content_lower)
                if age_match:
                    profile['age'] = int(age_match.group(1))

            if 'pax' in content_lower and is_from_me == 0:
                if '2' in content and 'pax' in content_lower:
                    profile['pax'] = 2
                elif '1' in content and 'pax' in content_lower:
                    profile['pax'] = 1

            if 'budget' in content_lower and is_from_me == 0:
                budget_match = re.search(r'\$?\s*(\d+(?:,\d+)*)', content)
                if budget_match:
                    profile['budget'] = budget_match.group(1)

        return profile

    def generate_coordinator_brief(self, property_name, coordinator_name, coordinator_phone, viewers):
        """Generate a viewing confirmation brief for the coordinator"""
        brief = f"""VIEWING CONFIRMATION BRIEF

Property: {property_name}
Coordinator: {coordinator_name} ({coordinator_phone})
Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

CONFIRMED VIEWERS: {len(viewers)}
────────────────────────────────────────────────

"""

        for idx, viewer in enumerate(viewers, 1):
            brief += f"""{idx}. {viewer['name']}
   Phone: {viewer['phone'] or 'Not provided'}
   Time: {viewer['viewing_time'] or 'TBD'}
   Details: {viewer['nationality'] or '?'}/{viewer['gender'] or '?'}/{viewer['age'] or '?'}
   Status: {viewer['confirmation'] or 'Pending'}
   Move in: {viewer['move_in'] or 'Not specified'}

"""

        return brief

    def generate_confirmation_message(self, viewer, property_name, coordinator_name, coordinator_phone, address, viewing_date):
        """Generate personalized confirmation message for a viewer"""
        name = viewer['name']
        viewing_time = viewer['viewing_time'] or 'TBD'

        # Determine ID type to suggest
        id_suggestion = "Your Student Pass/ID" if viewer['pass_type'] and 'student' in viewer['pass_type'].lower() else "Your ID/Passport"

        message = f"""Hi {name.split()[0]},

Your viewing for {property_name} on Thursday is confirmed!

VIEWING DETAILS:
Date: {viewing_date}
Time: {viewing_time}
Property: {property_name} Lakeside Condo
Address: {address}

YOUR COORDINATOR:
{coordinator_name} will show you the available rooms.
Contact: {coordinator_phone}

What to bring:
✓ {id_suggestion}

If you have any questions, you can reach {coordinator_name} directly on WhatsApp.

Looking forward to showing you the property!"""

        return message

    def send_confirmations(self, viewers, property_name, coordinator_name, coordinator_phone, address, viewing_date):
        """Send confirmation messages to all viewers"""
        results = {
            'sent': [],
            'failed': []
        }

        for viewer in viewers:
            message = self.generate_confirmation_message(
                viewer, property_name, coordinator_name, coordinator_phone, address, viewing_date
            )

            try:
                r = requests.post(
                    self.wa_api,
                    json={"recipient": viewer['jid'], "message": message},
                    timeout=15
                )

                if r.status_code in (200, 201):
                    results['sent'].append(viewer['name'])
                else:
                    results['failed'].append({
                        'name': viewer['name'],
                        'error': f"HTTP {r.status_code}"
                    })
            except Exception as e:
                results['failed'].append({
                    'name': viewer['name'],
                    'error': str(e)
                })

        return results

    def close(self):
        """Close database connection"""
        self.conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Viewing Confirmation Skill v2 - Send personalized confirmations to all viewers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Send confirmations with all details
  python3 viewing_confirmation_skill.py --property "Caspian" --coordinator "Don Chuang" \\
    --phone "+65 8526 0116" --address "Block 60, #16-44" --date "Thursday, June 10, 2026" --send

  # Generate brief only (no send)
  python3 viewing_confirmation_skill.py --property "Caspian" --coordinator "Don Chuang" \\
    --phone "+65 8526 0116" --brief

  # Export viewer data as JSON
  python3 viewing_confirmation_skill.py --property "Caspian" --output json
        """)

    parser.add_argument("--property", required=True, help="Property name (e.g., Caspian)")
    parser.add_argument("--coordinator", required=True, help="Coordinator name")
    parser.add_argument("--phone", required=True, help="Coordinator phone number")
    parser.add_argument("--address", help="Property address (e.g., Block 60, #16-44)")
    parser.add_argument("--date", default="Thursday, June 10, 2026", help="Viewing date")
    parser.add_argument("--output", choices=['brief', 'json', 'messages'], default='brief')
    parser.add_argument("--send", action='store_true', help="Send messages via WhatsApp")
    parser.add_argument("--brief", action='store_true', help="Generate coordinator brief only")
    parser.add_argument("--days", type=int, default=7, help="Look back days (default: 7)")

    args = parser.parse_args()

    vc = ViewingConfirmationSkill()

    # Get confirmed viewers
    confirmed_jids = vc.get_confirmed_viewers(args.property, args.days)

    if not confirmed_jids:
        print(f"No confirmed viewers found for {args.property}")
        vc.close()
        return

    # Extract profiles
    viewers = []
    for jid, last_msg in confirmed_jids:
        profile = vc.extract_prospect_profile(jid)
        if profile['confirmation']:  # Only include confirmed viewers
            viewers.append(profile)

    if not viewers:
        print(f"No confirmed viewers found for {args.property}")
        vc.close()
        return

    # Generate coordinator brief
    if args.brief:
        brief = vc.generate_coordinator_brief(args.property, args.coordinator, args.phone, viewers)
        print(brief)

    # Send messages
    if args.send and args.address:
        print(f"\nSending {len(viewers)} confirmation messages...\n")
        results = vc.send_confirmations(viewers, args.property, args.coordinator, args.phone, args.address, args.date)

        print(f"✅ Sent: {len(results['sent'])}")
        for name in results['sent']:
            print(f"   • {name}")

        if results['failed']:
            print(f"\n❌ Failed: {len(results['failed'])}")
            for item in results['failed']:
                print(f"   • {item['name']}: {item['error']}")

    # Export as JSON
    if args.output == 'json':
        print(json.dumps(viewers, indent=2))

    # Output messages for review
    if args.output == 'messages':
        for viewer in viewers:
            message = vc.generate_confirmation_message(
                viewer, args.property, args.coordinator, args.phone,
                args.address or "TBD", args.date
            )
            print(f"\n{'='*80}")
            print(f"TO: {viewer['name']}")
            print('='*80)
            print(message)

    vc.close()


if __name__ == "__main__":
    main()
