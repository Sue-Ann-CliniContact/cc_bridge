from django.contrib.auth.models import User
from django.db import models


class ClientProfile(models.Model):
    """Links a Django User to their Monday.com identity + OAuth token."""

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    monday_id = models.CharField(max_length=100, blank=True, null=True)
    access_token = models.CharField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.user.username
