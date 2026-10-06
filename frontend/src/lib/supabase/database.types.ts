export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[];

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.18";
  };
  public: {
    Tables: {
      ai_runs: {
        Row: {
          error_message: string | null;
          exam_id: string | null;
          exam_project_id: string;
          finished_at: string | null;
          heartbeat_at: string;
          id: string;
          input_tokens: number;
          kind: string;
          output_tokens: number;
          professor_id: string;
          stage: string | null;
          started_at: string;
          status: string;
        };
        Insert: {
          error_message?: string | null;
          exam_id?: string | null;
          exam_project_id: string;
          finished_at?: string | null;
          heartbeat_at?: string;
          id?: string;
          input_tokens?: number;
          kind: string;
          output_tokens?: number;
          professor_id?: string;
          stage?: string | null;
          started_at?: string;
          status?: string;
        };
        Update: {
          error_message?: string | null;
          exam_id?: string | null;
          exam_project_id?: string;
          finished_at?: string | null;
          heartbeat_at?: string;
          id?: string;
          input_tokens?: number;
          kind?: string;
          output_tokens?: number;
          professor_id?: string;
          stage?: string | null;
          started_at?: string;
          status?: string;
        };
        Relationships: [
          {
            foreignKeyName: "ai_runs_exam_id_fkey";
            columns: ["exam_id"];
            isOneToOne: false;
            referencedRelation: "exams";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "ai_runs_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: false;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "ai_runs_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      assessment_style_profiles: {
        Row: {
          created_at: string;
          exam_project_id: string;
          id: string;
          model: string | null;
          professor_id: string;
          profile: Json;
          source_document_ids: string[];
          source_hash: string;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          exam_project_id: string;
          id?: string;
          model?: string | null;
          professor_id?: string;
          profile: Json;
          source_document_ids: string[];
          source_hash: string;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          exam_project_id?: string;
          id?: string;
          model?: string | null;
          professor_id?: string;
          profile?: Json;
          source_document_ids?: string[];
          source_hash?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "assessment_style_profiles_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: true;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "assessment_style_profiles_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      courses: {
        Row: {
          code: string;
          created_at: string;
          description: string | null;
          id: string;
          name: string | null;
          professor_id: string;
          updated_at: string;
        };
        Insert: {
          code: string;
          created_at?: string;
          description?: string | null;
          id?: string;
          name?: string | null;
          professor_id?: string;
          updated_at?: string;
        };
        Update: {
          code?: string;
          created_at?: string;
          description?: string | null;
          id?: string;
          name?: string | null;
          professor_id?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "courses_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      document_chunks: {
        Row: {
          category: string;
          chunk_index: number;
          content: string;
          created_at: string;
          document_id: string;
          embedding: string | null;
          exam_project_id: string | null;
          id: string;
          location_label: string | null;
          page_end: number | null;
          page_start: number | null;
          professor_id: string;
          token_estimate: number | null;
        };
        Insert: {
          category: string;
          chunk_index: number;
          content: string;
          created_at?: string;
          document_id: string;
          embedding?: string | null;
          exam_project_id?: string | null;
          id?: string;
          location_label?: string | null;
          page_end?: number | null;
          page_start?: number | null;
          professor_id?: string;
          token_estimate?: number | null;
        };
        Update: {
          category?: string;
          chunk_index?: number;
          content?: string;
          created_at?: string;
          document_id?: string;
          embedding?: string | null;
          exam_project_id?: string | null;
          id?: string;
          location_label?: string | null;
          page_end?: number | null;
          page_start?: number | null;
          professor_id?: string;
          token_estimate?: number | null;
        };
        Relationships: [
          {
            foreignKeyName: "document_chunks_document_id_fkey";
            columns: ["document_id"];
            isOneToOne: false;
            referencedRelation: "documents";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "document_chunks_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: false;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "document_chunks_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      documents: {
        Row: {
          category: string;
          content_sha256: string | null;
          course_id: string | null;
          created_at: string;
          exam_project_id: string | null;
          extracted_characters: number | null;
          id: string;
          mime_type: string | null;
          original_name: string;
          page_count: number | null;
          processed_at: string | null;
          processing_error: string | null;
          processing_status: string;
          professor_id: string;
          size_bytes: number | null;
          storage_path: string;
          summary: Json | null;
        };
        Insert: {
          category: string;
          content_sha256?: string | null;
          course_id?: string | null;
          created_at?: string;
          exam_project_id?: string | null;
          extracted_characters?: number | null;
          id?: string;
          mime_type?: string | null;
          original_name: string;
          page_count?: number | null;
          processed_at?: string | null;
          processing_error?: string | null;
          processing_status?: string;
          professor_id?: string;
          size_bytes?: number | null;
          storage_path: string;
          summary?: Json | null;
        };
        Update: {
          category?: string;
          content_sha256?: string | null;
          course_id?: string | null;
          created_at?: string;
          exam_project_id?: string | null;
          extracted_characters?: number | null;
          id?: string;
          mime_type?: string | null;
          original_name?: string;
          page_count?: number | null;
          processed_at?: string | null;
          processing_error?: string | null;
          processing_status?: string;
          professor_id?: string;
          size_bytes?: number | null;
          storage_path?: string;
          summary?: Json | null;
        };
        Relationships: [
          {
            foreignKeyName: "documents_course_id_fkey";
            columns: ["course_id"];
            isOneToOne: false;
            referencedRelation: "courses";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "documents_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: false;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "documents_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exam_builder_messages: {
        Row: {
          channel: string;
          content: string;
          created_at: string;
          exam_id: string;
          id: string;
          professor_id: string;
          role: string;
        };
        Insert: {
          channel?: string;
          content: string;
          created_at?: string;
          exam_id: string;
          id?: string;
          professor_id?: string;
          role: string;
        };
        Update: {
          channel?: string;
          content?: string;
          created_at?: string;
          exam_id?: string;
          id?: string;
          professor_id?: string;
          role?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_builder_messages_exam_id_fkey";
            columns: ["exam_id"];
            isOneToOne: false;
            referencedRelation: "exams";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_builder_messages_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exam_projects: {
        Row: {
          additional_notes: string | null;
          course_id: string | null;
          created_at: string;
          difficulty: string | null;
          duration_minutes: number | null;
          easy_percentage: number | null;
          enhanced_prompt: string | null;
          exam_name: string | null;
          exam_spec: Json | null;
          generation_mode: string | null;
          hard_percentage: number | null;
          id: string;
          mcq_percentage: number | null;
          medium_percentage: number | null;
          number_of_versions: number;
          professor_id: string;
          professor_prompt: string | null;
          spec_approved_at: string | null;
          spec_generated_at: string | null;
          spec_status: string;
          status: string;
          subjective_percentage: number | null;
          updated_at: string;
        };
        Insert: {
          additional_notes?: string | null;
          course_id?: string | null;
          created_at?: string;
          difficulty?: string | null;
          duration_minutes?: number | null;
          easy_percentage?: number | null;
          enhanced_prompt?: string | null;
          exam_name?: string | null;
          exam_spec?: Json | null;
          generation_mode?: string | null;
          hard_percentage?: number | null;
          id?: string;
          mcq_percentage?: number | null;
          medium_percentage?: number | null;
          number_of_versions?: number;
          professor_id?: string;
          professor_prompt?: string | null;
          spec_approved_at?: string | null;
          spec_generated_at?: string | null;
          spec_status?: string;
          status?: string;
          subjective_percentage?: number | null;
          updated_at?: string;
        };
        Update: {
          additional_notes?: string | null;
          course_id?: string | null;
          created_at?: string;
          difficulty?: string | null;
          duration_minutes?: number | null;
          easy_percentage?: number | null;
          enhanced_prompt?: string | null;
          exam_name?: string | null;
          exam_spec?: Json | null;
          generation_mode?: string | null;
          hard_percentage?: number | null;
          id?: string;
          mcq_percentage?: number | null;
          medium_percentage?: number | null;
          number_of_versions?: number;
          professor_id?: string;
          professor_prompt?: string | null;
          spec_approved_at?: string | null;
          spec_generated_at?: string | null;
          spec_status?: string;
          status?: string;
          subjective_percentage?: number | null;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_projects_course_id_fkey";
            columns: ["course_id"];
            isOneToOne: false;
            referencedRelation: "courses";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_projects_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exam_questions: {
        Row: {
          answer: string | null;
          choices: Json | null;
          concepts: string[];
          correct_choice: string | null;
          created_at: string;
          difficulty: string;
          estimated_minutes: number | null;
          exam_id: string;
          explanation: string | null;
          figure_document_id: string | null;
          id: string;
          learning_objectives: string[];
          needs_solution_review: boolean;
          points: number;
          position: number;
          professor_id: string;
          prompt: string;
          rubric: Json | null;
          section_id: string | null;
          slot_id: string;
          solution: string | null;
          source_refs: Json;
          status: string;
          subparts: Json | null;
          type: string;
          updated_at: string;
          version_id: string;
        };
        Insert: {
          answer?: string | null;
          choices?: Json | null;
          concepts?: string[];
          correct_choice?: string | null;
          created_at?: string;
          difficulty: string;
          estimated_minutes?: number | null;
          exam_id: string;
          explanation?: string | null;
          figure_document_id?: string | null;
          id?: string;
          learning_objectives?: string[];
          needs_solution_review?: boolean;
          points: number;
          position: number;
          professor_id?: string;
          prompt: string;
          rubric?: Json | null;
          section_id?: string | null;
          slot_id?: string;
          solution?: string | null;
          source_refs?: Json;
          status?: string;
          subparts?: Json | null;
          type: string;
          updated_at?: string;
          version_id: string;
        };
        Update: {
          answer?: string | null;
          choices?: Json | null;
          concepts?: string[];
          correct_choice?: string | null;
          created_at?: string;
          difficulty?: string;
          estimated_minutes?: number | null;
          exam_id?: string;
          explanation?: string | null;
          figure_document_id?: string | null;
          id?: string;
          learning_objectives?: string[];
          needs_solution_review?: boolean;
          points?: number;
          position?: number;
          professor_id?: string;
          prompt?: string;
          rubric?: Json | null;
          section_id?: string | null;
          slot_id?: string;
          solution?: string | null;
          source_refs?: Json;
          status?: string;
          subparts?: Json | null;
          type?: string;
          updated_at?: string;
          version_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_questions_exam_id_fkey";
            columns: ["exam_id"];
            isOneToOne: false;
            referencedRelation: "exams";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_questions_figure_document_id_fkey";
            columns: ["figure_document_id"];
            isOneToOne: false;
            referencedRelation: "documents";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_questions_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_questions_section_fkey";
            columns: ["section_id", "exam_id"];
            isOneToOne: false;
            referencedRelation: "exam_sections";
            referencedColumns: ["id", "exam_id"];
          },
          {
            foreignKeyName: "exam_questions_version_fkey";
            columns: ["version_id", "exam_id"];
            isOneToOne: false;
            referencedRelation: "exam_versions";
            referencedColumns: ["id", "exam_id"];
          },
        ];
      };
      exam_sections: {
        Row: {
          created_at: string;
          exam_id: string;
          id: string;
          instructions: string | null;
          position: number;
          professor_id: string;
          title: string;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          exam_id: string;
          id?: string;
          instructions?: string | null;
          position: number;
          professor_id?: string;
          title: string;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          exam_id?: string;
          id?: string;
          instructions?: string | null;
          position?: number;
          professor_id?: string;
          title?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_sections_exam_id_fkey";
            columns: ["exam_id"];
            isOneToOne: false;
            referencedRelation: "exams";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_sections_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exam_versions: {
        Row: {
          created_at: string;
          exam_id: string;
          id: string;
          label: string;
          position: number;
          professor_id: string;
        };
        Insert: {
          created_at?: string;
          exam_id: string;
          id?: string;
          label: string;
          position: number;
          professor_id?: string;
        };
        Update: {
          created_at?: string;
          exam_id?: string;
          id?: string;
          label?: string;
          position?: number;
          professor_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exam_versions_exam_id_fkey";
            columns: ["exam_id"];
            isOneToOne: false;
            referencedRelation: "exams";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exam_versions_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      exams: {
        Row: {
          created_at: string;
          enhanced_prompt_snapshot: string | null;
          error_message: string | null;
          exam_project_id: string;
          finalized_at: string | null;
          id: string;
          mode: string;
          plan: Json | null;
          professor_id: string;
          review: Json | null;
          review_status: string;
          reviewed_at: string | null;
          spec_snapshot: Json;
          status: string;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          enhanced_prompt_snapshot?: string | null;
          error_message?: string | null;
          exam_project_id: string;
          finalized_at?: string | null;
          id?: string;
          mode: string;
          plan?: Json | null;
          professor_id?: string;
          review?: Json | null;
          review_status?: string;
          reviewed_at?: string | null;
          spec_snapshot: Json;
          status?: string;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          enhanced_prompt_snapshot?: string | null;
          error_message?: string | null;
          exam_project_id?: string;
          finalized_at?: string | null;
          id?: string;
          mode?: string;
          plan?: Json | null;
          professor_id?: string;
          review?: Json | null;
          review_status?: string;
          reviewed_at?: string | null;
          spec_snapshot?: Json;
          status?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "exams_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: true;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "exams_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      preference_signals: {
        Row: {
          created_at: string;
          exam_project_id: string | null;
          id: string;
          kind: string;
          payload: Json;
          professor_id: string;
        };
        Insert: {
          created_at?: string;
          exam_project_id?: string | null;
          id?: string;
          kind: string;
          payload: Json;
          professor_id?: string;
        };
        Update: {
          created_at?: string;
          exam_project_id?: string | null;
          id?: string;
          kind?: string;
          payload?: Json;
          professor_id?: string;
        };
        Relationships: [
          {
            foreignKeyName: "preference_signals_exam_project_id_fkey";
            columns: ["exam_project_id"];
            isOneToOne: false;
            referencedRelation: "exam_projects";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "preference_signals_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      professor_preferences: {
        Row: {
          created_at: string;
          explicit_notes: string | null;
          learned: Json;
          learning_enabled: boolean;
          professor_id: string;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          explicit_notes?: string | null;
          learned?: Json;
          learning_enabled?: boolean;
          professor_id?: string;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          explicit_notes?: string | null;
          learned?: Json;
          learning_enabled?: boolean;
          professor_id?: string;
          updated_at?: string;
        };
        Relationships: [
          {
            foreignKeyName: "professor_preferences_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: true;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
        ];
      };
      profiles: {
        Row: {
          created_at: string;
          department: string | null;
          full_name: string | null;
          id: string;
          institution: string | null;
          updated_at: string;
        };
        Insert: {
          created_at?: string;
          department?: string | null;
          full_name?: string | null;
          id: string;
          institution?: string | null;
          updated_at?: string;
        };
        Update: {
          created_at?: string;
          department?: string | null;
          full_name?: string | null;
          id?: string;
          institution?: string | null;
          updated_at?: string;
        };
        Relationships: [];
      };
      question_revisions: {
        Row: {
          created_at: string;
          id: string;
          instruction: string | null;
          professor_id: string;
          question_id: string;
          revision_number: number;
          snapshot: Json;
          source: string;
        };
        Insert: {
          created_at?: string;
          id?: string;
          instruction?: string | null;
          professor_id?: string;
          question_id: string;
          revision_number: number;
          snapshot: Json;
          source: string;
        };
        Update: {
          created_at?: string;
          id?: string;
          instruction?: string | null;
          professor_id?: string;
          question_id?: string;
          revision_number?: number;
          snapshot?: Json;
          source?: string;
        };
        Relationships: [
          {
            foreignKeyName: "question_revisions_professor_id_fkey";
            columns: ["professor_id"];
            isOneToOne: false;
            referencedRelation: "profiles";
            referencedColumns: ["id"];
          },
          {
            foreignKeyName: "question_revisions_question_id_fkey";
            columns: ["question_id"];
            isOneToOne: false;
            referencedRelation: "exam_questions";
            referencedColumns: ["id"];
          },
        ];
      };
    };
    Views: {
      [_ in never]: never;
    };
    Functions: {
      match_document_chunks: {
        Args: {
          p_categories?: string[];
          p_exam_project_id: string;
          p_match_count?: number;
          p_query_embedding: string;
        };
        Returns: {
          chunk_index: number;
          content: string;
          document_id: string;
          id: string;
          location_label: string;
          page_end: number;
          page_start: number;
          similarity: number;
        }[];
      };
      reorder_exam_questions: {
        Args: { p_question_ids: string[]; p_version_id: string };
        Returns: undefined;
      };
    };
    Enums: {
      [_ in never]: never;
    };
    CompositeTypes: {
      [_ in never]: never;
    };
  };
};

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">;

type DefaultSchema = DatabaseWithoutInternals[Extract<
  keyof Database,
  "public"
>];

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R;
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R;
      }
      ? R
      : never
    : never;

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I;
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I;
      }
      ? I
      : never
    : never;

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    keyof DefaultSchema["Tables"] | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U;
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U;
      }
      ? U
      : never
    : never;

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    keyof DefaultSchema["Enums"] | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never;

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals;
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals;
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never;

export const Constants = {
  public: {
    Enums: {},
  },
} as const;
