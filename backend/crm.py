import logging
from typing import Optional
import config

logger = logging.getLogger("novamart")


def create_hubspot_contact(
    name: str,
    email: str,
    message: str,
    session_id: str,
    conversation_history: list[dict]
) -> Optional[str]:
    """
    Crée ou met à jour un contact dans HubSpot CRM.
    Retourne l'ID du contact créé ou None si échec.
    """
    if not config.HUBSPOT_API_KEY:
        logger.warning("HUBSPOT_API_KEY non configuré — contact non créé")
        return None

    try:
        from hubspot import HubSpot
        from hubspot.crm.contacts import SimplePublicObjectInputForCreate
        from hubspot.crm.contacts.exceptions import ApiException

        client = HubSpot(access_token=config.HUBSPOT_API_KEY)

        # Formate l'historique de conversation
        history_text = ""
        if conversation_history:
            history_text = "\n\n--- Historique conversation ---\n"
            for msg in conversation_history[-10:]:
                role = "Client" if msg["role"] == "user" else "Bot"
                content = msg["content"][:200]
                history_text += f"{role}: {content}\n"

        # Prépare les propriétés du contact
        firstname = name.split()[0] if name else "Inconnu"
        lastname = " ".join(name.split()[1:]) if len(name.split()) > 1 else ""

        properties = {
            "firstname": firstname,
            "lastname": lastname,
            "email": email,
            "hs_lead_status": "NEW",
            "lifecyclestage": "lead",
            "message": f"Session: {session_id}\n\nMessage: {message}{history_text}",
            "hs_content_membership_notes": f"Contact via NovaMart Support Chatbot\nSession ID: {session_id}"
        }

        # Tente de créer le contact
        try:
            contact_input = SimplePublicObjectInputForCreate(properties=properties)
            response = client.crm.contacts.basic_api.create(
                simple_public_object_input_for_create=contact_input
            )
            contact_id = response.id
            logger.info("hubspot_contact_created", extra={
                "extra": {"contact_id": contact_id, "email": email}
            })
            return contact_id

        except ApiException as e:
            # Si contact existe déjà (409), cherche et met à jour
            if e.status == 409:
                search_response = client.crm.contacts.search_api.do_search(
                    public_object_search_request={
                        "filterGroups": [{
                            "filters": [{
                                "propertyName": "email",
                                "operator": "EQ",
                                "value": email
                            }]
                        }],
                        "limit": 1
                    }
                )
                if search_response.results:
                    contact_id = search_response.results[0].id
                    client.crm.contacts.basic_api.update(
                        contact_id=contact_id,
                        simple_public_object_input={"properties": {
                            "message": properties["message"]
                        }}
                    )
                    logger.info("hubspot_contact_updated", extra={
                        "extra": {"contact_id": contact_id, "email": email}
                    })
                    return contact_id
            raise

    except Exception as e:
        logger.error(f"hubspot_error: {str(e)}")
        return None
