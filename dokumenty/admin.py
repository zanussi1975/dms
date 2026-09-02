from django.contrib import admin
from .models import Document, DocumentHistory, WorkflowRule
from .models import SystemNote
from django.utils import timezone

@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    # Co chcemy widzieć na głównej liście faktur
    list_display = (
        'document_number', 
        'typ_dokumentu',
        'contractor_name', 
        'gross_amount', 
        'status', 
        'assigned_to', 
        'created_at'
    )
    
    # Panele filtrowania po prawej stronie
    list_filter = ('status', 'assigned_to', 'created_at')
    
    # Pola, po których działa górna wyszukiwarka
    search_fields = ('document_number', 'ksef_number', 'contractor_nip', 'contractor_name')
    
    # Opcjonalnie: pola tylko do odczytu (np. data utworzenia)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(DocumentHistory)
class DocumentHistoryAdmin(admin.ModelAdmin):
    list_display = ('document', 'user', 'previous_status', 'new_status', 'created_at')
    list_filter = ('new_status', 'created_at')
    search_fields = ('document__document_number', 'comment')
    
    # Ślad rewizyjny (Audit Trail) z zasady nie powinien być edytowalny
    readonly_fields = ('document', 'user', 'previous_status', 'new_status', 'comment', 'created_at')
    

@admin.register(WorkflowRule)
class WorkflowRuleAdmin(admin.ModelAdmin):
    list_display = ('target_status', 'default_assignee')
    list_filter = ('target_status',)
    search_fields = ('default_assignee__username', 'default_assignee__last_name')
    

@admin.register(SystemNote)
class SystemNoteAdmin(admin.ModelAdmin):
    list_display = ('user', 'created_at', 'is_resolved', 'responded_at')
    list_filter = ('is_resolved', 'created_at')
    search_fields = ('content', 'admin_response', 'user__username', 'user__first_name', 'user__last_name')
    readonly_fields = ('user', 'content', 'created_at', 'responded_at')
    
    # Automatyczne nadawanie daty odpowiedzi i statusu "Rozwiązane" gdy wpiszesz komentarz
    def save_model(self, request, obj, form, change):
        if change and 'admin_response' in form.changed_data and obj.admin_response:
            obj.responded_at = timezone.now()
            obj.is_resolved = True
        super().save_model(request, obj, form, change)
